"""
wolfpole_core.py — infrastructure layer for Wolfpole.

Wolfpole is a rewrite of Madpole (itself a fork of Tadpole, built on tzlion's
frogtool) for managing SF2000 SD cards.

This module owns everything that is *not* window chrome:

  * a Qt binding shim so the app runs on PyQt5 **or** PyQt6
  * application paths, rotating logs, and a typed config facade
  * optional-dependency handling (missing modules degrade, they do not crash)
  * a cancellable background job system on top of QThreadPool
  * a drive watcher that only emits when the set of drives actually changes
  * an off-thread RGB565 thumbnail decoder with an mtime-keyed LRU cache
  * a ROM table model + filter proxy (sortable, searchable, virtualised)
  * a trash bin so ROM deletion is undoable
  * safe filesystem helpers (atomic writes, protected-path guard, free space)
  * a hand-written dark/light stylesheet

Nothing in here imports the GUI module, so it is importable headless.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

APP_NAME = "Wolfpole"
APP_SLUG = "wolfpole"
APP_VERSION = "1.0.0"
APP_TAGLINE = "SF2000 SD card manager"

log = logging.getLogger(APP_SLUG)


# ---------------------------------------------------------------------------
# Qt binding shim
# ---------------------------------------------------------------------------
#
# Madpole hard-bound to PyQt5 with `from PyQt5.QtWidgets import *`, which both
# pins the binding and pollutes the namespace (QTimer, QSize and ~300 other
# names arriving unqualified). Wolfpole imports the three modules explicitly
# and resolves the handful of API differences between PyQt5 and PyQt6 here.

try:  # pragma: no cover - depends on what is installed
    from PyQt6 import QtCore, QtGui, QtWidgets

    QT_BINDING = "PyQt6"
except ImportError:  # pragma: no cover
    from PyQt5 import QtCore, QtGui, QtWidgets

    QT_BINDING = "PyQt5"

Qt = QtCore.Qt
Signal = QtCore.pyqtSignal
Slot = QtCore.pyqtSlot

# PyQt6 moved QAction from QtWidgets to QtGui.
QAction = getattr(QtGui, "QAction", None) or QtWidgets.QAction
QShortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut


def qenum(owner: Any, scope: str, name: str) -> Any:
    """Resolve ``owner.scope.name`` with a fallback to ``owner.name``.

    PyQt6 requires fully scoped enum access; PyQt5 mostly supports it but not
    universally. This tries the scoped form first and degrades gracefully.
    """
    holder = getattr(owner, scope, None)
    if holder is not None:
        member = getattr(holder, name, None)
        if member is not None:
            return member
    return getattr(owner, name)


def qexec(obj: Any) -> Any:
    """Call exec() on a dialog/app across bindings."""
    fn = getattr(obj, "exec", None) or getattr(obj, "exec_")
    return fn()


# Enum constants, resolved once. Call sites stay readable and binding-proof.
ALIGN_CENTER = qenum(Qt, "AlignmentFlag", "AlignCenter")
ALIGN_VCENTER = qenum(Qt, "AlignmentFlag", "AlignVCenter")
ALIGN_LEFT = qenum(Qt, "AlignmentFlag", "AlignLeft")
ALIGN_RIGHT = qenum(Qt, "AlignmentFlag", "AlignRight")
ALIGN_LEFT_VCENTER = ALIGN_LEFT | ALIGN_VCENTER
ALIGN_RIGHT_VCENTER = ALIGN_RIGHT | ALIGN_VCENTER

ROLE_DISPLAY = qenum(Qt, "ItemDataRole", "DisplayRole")
ROLE_DECORATION = qenum(Qt, "ItemDataRole", "DecorationRole")
ROLE_EDIT = qenum(Qt, "ItemDataRole", "EditRole")
ROLE_TOOLTIP = qenum(Qt, "ItemDataRole", "ToolTipRole")
ROLE_TEXT_ALIGN = qenum(Qt, "ItemDataRole", "TextAlignmentRole")
ROLE_USER = qenum(Qt, "ItemDataRole", "UserRole")
ROLE_SORT = ROLE_USER + 1
ROLE_FILTER = ROLE_USER + 2

FLAG_ENABLED = qenum(Qt, "ItemFlag", "ItemIsEnabled")
FLAG_SELECTABLE = qenum(Qt, "ItemFlag", "ItemIsSelectable")
FLAG_EDITABLE = qenum(Qt, "ItemFlag", "ItemIsEditable")

ORIENT_HORIZONTAL = qenum(Qt, "Orientation", "Horizontal")
ORIENT_VERTICAL = qenum(Qt, "Orientation", "Vertical")

SORT_ASCENDING = qenum(Qt, "SortOrder", "AscendingOrder")
CASE_INSENSITIVE = qenum(Qt, "CaseSensitivity", "CaseInsensitive")

FORMAT_RGB16 = qenum(QtGui.QImage, "Format", "Format_RGB16")
SMOOTH_TRANSFORM = qenum(Qt, "TransformationMode", "SmoothTransformation")
KEEP_ASPECT_BY_EXPANDING = qenum(Qt, "AspectRatioMode", "KeepAspectRatioByExpanding")
KEEP_ASPECT = qenum(Qt, "AspectRatioMode", "KeepAspectRatio")

MB_YES = qenum(QtWidgets.QMessageBox, "StandardButton", "Yes")
MB_NO = qenum(QtWidgets.QMessageBox, "StandardButton", "No")
MB_OK = qenum(QtWidgets.QMessageBox, "StandardButton", "Ok")
MB_CANCEL = qenum(QtWidgets.QMessageBox, "StandardButton", "Cancel")

RESIZE_INTERACTIVE = qenum(QtWidgets.QHeaderView, "ResizeMode", "Interactive")
RESIZE_TO_CONTENTS = qenum(QtWidgets.QHeaderView, "ResizeMode", "ResizeToContents")
RESIZE_STRETCH = qenum(QtWidgets.QHeaderView, "ResizeMode", "Stretch")
RESIZE_FIXED = qenum(QtWidgets.QHeaderView, "ResizeMode", "Fixed")

SELECT_ROWS = qenum(QtWidgets.QAbstractItemView, "SelectionBehavior", "SelectRows")
SELECT_EXTENDED = qenum(QtWidgets.QAbstractItemView, "SelectionMode", "ExtendedSelection")
SCROLL_PER_PIXEL = qenum(QtWidgets.QAbstractItemView, "ScrollMode", "ScrollPerPixel")

DOCK_RIGHT = qenum(Qt, "DockWidgetArea", "RightDockWidgetArea")
TOOLBUTTON_TEXT_BESIDE = qenum(Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon")
CONTEXT_MENU_CUSTOM = qenum(Qt, "ContextMenuPolicy", "CustomContextMenu")

CURSOR_WAIT = qenum(Qt, "CursorShape", "WaitCursor")

#: Worker threads emit into the GUI thread. Plain Python callables have no
#: receiver QObject, so Qt would pick a *direct* connection and run the slot on
#: the worker thread -- which is how "random" GUI crashes happen. Every job
#: callback is therefore connected explicitly as queued.
QUEUED = qenum(Qt, "ConnectionType", "QueuedConnection")


def std_icon(widget: QtWidgets.QWidget, name: str) -> QtGui.QIcon:
    """Fetch a platform standard icon by SP_* name, tolerating unknown names."""
    style = widget.style()
    try:
        pixmap = qenum(QtWidgets.QStyle, "StandardPixmap", name)
    except AttributeError:
        return QtGui.QIcon()
    return style.standardIcon(pixmap)


# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
#
# Madpole imported fourteen sibling modules at the top of the file. One missing
# dialog meant the whole program refused to start with a traceback. Wolfpole
# records what is missing and lets the UI grey out just that feature.

_OPTIONAL_CACHE: dict[str, Any] = {}
MISSING: dict[str, str] = {}


def optional_import(dotted: str, attr: str | None = None) -> Any | None:
    """Import a module (or attribute) or return None, remembering the failure."""
    key = f"{dotted}:{attr}" if attr else dotted
    if key in _OPTIONAL_CACHE:
        return _OPTIONAL_CACHE[key]
    try:
        module = import_module(dotted)
        value = getattr(module, attr) if attr else module
    except Exception as exc:  # noqa: BLE001 - any failure means "unavailable"
        MISSING[key] = f"{type(exc).__name__}: {exc}"
        log.warning("Optional dependency unavailable: %s (%s)", key, exc)
        value = None
    _OPTIONAL_CACHE[key] = value
    return value


def have(*keys: str) -> bool:
    """True when every named optional dependency imported successfully."""
    return all(_OPTIONAL_CACHE.get(k) is not None for k in keys)


# ---------------------------------------------------------------------------
# SF2000 domain constants
# ---------------------------------------------------------------------------

#: Thumbnails stored in the header of .zxx/.zfb files are 144x208 RGB565.
THUMB_W, THUMB_H = 144, 208
THUMB_BYTES = THUMB_W * THUMB_H * 2

#: Fallback when frogtool is unavailable.
DEFAULT_SYSTEMS = ["FC", "SFC", "MD", "GB", "GBC", "GBA", "ARCADE"]

DEFAULT_ZXX_EXT = {
    "FC": "zfc",
    "SFC": "zsf",
    "MD": "zmd",
    "GB": "zgb",
    "GBC": "zgb",
    "GBA": "zgb",
    "ARCADE": "zfb",
}

SYSTEM_LABELS = {
    "FC": "NES / Famicom",
    "SFC": "SNES / Super Famicom",
    "MD": "Mega Drive / Genesis",
    "GB": "Game Boy",
    "GBC": "Game Boy Color",
    "GBA": "Game Boy Advance",
    "ARCADE": "Arcade",
}

ROM_EXTENSIONS = (
    ".zip .zfc .zsf .zmd .zgb .zfb .smc .fig .sfc .gd3 .gd7 .dx2 .bsx .swc "
    ".nes .nfc .fds .unf .gbc .gb .sgb .gba .agb .gbz .bin .md .smd .gen .sms "
    ".7z .nez .mgd .fam .68k"
).split()

ALL_SYSTEMS = "ALL"
NO_DRIVE = "N/A"

#: Paths we refuse to format, bulk-delete or overwrite, whatever the user asks.
PROTECTED_ROOTS = {
    "c:\\", "c:/", "/", "/home", "/usr", "/etc", "/var", "/bin", "/boot",
    "/system", "/windows", "/library", "/applications", "/users",
}


def system_label(system: str) -> str:
    return SYSTEM_LABELS.get(system, system)


def zxx_ext_for(system: str) -> str:
    frogtool = optional_import("frogtool")
    table = getattr(frogtool, "zxx_ext", None) if frogtool else None
    if isinstance(table, dict) and system in table:
        return str(table[system]).lower()
    return DEFAULT_ZXX_EXT.get(system, "")


def known_systems() -> list[str]:
    frogtool = optional_import("frogtool")
    table = getattr(frogtool, "systems", None) if frogtool else None
    if isinstance(table, dict) and table:
        return list(table.keys())
    tadpole_functions = optional_import("tadpole_functions")
    table = getattr(tadpole_functions, "systems", None) if tadpole_functions else None
    if isinstance(table, dict) and table:
        return list(table.keys())
    return list(DEFAULT_SYSTEMS)


# ---------------------------------------------------------------------------
# Paths, logging, config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppPaths:
    """Where Wolfpole keeps its own files.

    Madpole wrote its log and ini into ``os.getcwd()``, so the files landed
    wherever the shell happened to be and were lost when frozen or launched
    from a shortcut. Wolfpole uses a per-user directory by default and honours
    an explicit portable mode.
    """

    base: Path
    portable: bool

    @property
    def config_file(self) -> Path:
        return self.base / f"{APP_SLUG}.ini"

    @property
    def log_file(self) -> Path:
        return self.base / f"{APP_SLUG}.log"

    @property
    def cache_dir(self) -> Path:
        return self.base / "cache"

    @property
    def trash_dir(self) -> Path:
        return self.base / "trash"

    def ensure(self) -> "AppPaths":
        for directory in (self.base, self.cache_dir, self.trash_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def app_dir() -> Path:
    """Directory holding the application, whether run from source or frozen."""
    if getattr(sys, "frozen", False):  # PyInstaller and friends
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_paths(portable: bool | None = None) -> AppPaths:
    here = app_dir()
    if portable is None:
        portable = bool(os.environ.get("WOLFPOLE_PORTABLE")) or (here / "portable.txt").exists()
    if portable:
        return AppPaths(here / f"{APP_SLUG}-data", True).ensure()

    override = os.environ.get("WOLFPOLE_HOME")
    if override:
        return AppPaths(Path(override).expanduser(), False).ensure()

    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        base = root / APP_NAME
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        base = root / APP_SLUG
    return AppPaths(base, False).ensure()


def setup_logging(paths: AppPaths, verbose: bool = False) -> None:
    """Configure rotating file + console logging exactly once."""
    root = logging.getLogger()
    if getattr(root, "_wolfpole_configured", False):
        return

    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(funcName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        file_handler = RotatingFileHandler(
            paths.log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
    except OSError as exc:  # read-only media, permissions, ...
        print(f"{APP_NAME}: cannot write log file ({exc})", file=sys.stderr)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(console)

    root._wolfpole_configured = True  # type: ignore[attr-defined]
    log.info("%s %s starting (%s, Python %s, %s)", APP_NAME, APP_VERSION,
             QT_BINDING, platform.python_version(), platform.platform())


class Config:
    """Typed settings store, backed by an ini file.

    Wraps the project's own ``TadpoleConfig`` when it is importable so the
    existing dialogs keep working, while Wolfpole's own keys live in a
    separate file that is never clobbered by the legacy code.
    """

    SECTION = "wolfpole"

    DEFAULTS: dict[str, str] = {
        "theme": "auto",
        "thumbnails_in_table": "1",
        "thumbnail_scale": "70",
        "download_thumbnails": "1",
        "overwrite_thumbnails": "0",
        "confirm_delete": "1",
        "use_trash": "1",
        "poll_seconds": "2",
        "network": "1",
        "last_drive": "",
        "last_system": "",
        "local_library": "",
        "auto_rebuild": "1",
        "view_mode": "grid",
        "filter_mode": "all",
        "scope_all": "0",
    }

    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._parser = configparser.ConfigParser()
        self._parser.read(paths.config_file, encoding="utf-8")
        if not self._parser.has_section(self.SECTION):
            self._parser.add_section(self.SECTION)

        legacy_cls = optional_import("tadpoleConfig", "TadpoleConfig")
        self.legacy = None
        if legacy_cls is not None:
            try:
                self.legacy = legacy_cls()
            except Exception as exc:  # noqa: BLE001
                log.warning("TadpoleConfig could not be constructed: %s", exc)

        # Scratch attributes the legacy multicore dialogs read directly.
        self.cDir = ""
        self.cCon = ""
        self.gList: list[str] = []

    # -- raw access --------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str:
        fallback = self.DEFAULTS.get(key, "") if default is None else default
        return self._parser.get(self.SECTION, key, fallback=fallback)

    def set(self, key: str, value: Any) -> None:
        self._parser.set(self.SECTION, key, str(value))
        self.save()

    def get_bool(self, key: str) -> bool:
        return self.get(key).strip().lower() in {"1", "true", "yes", "on"}

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get(key)))
        except (TypeError, ValueError):
            return default

    def save(self) -> None:
        try:
            with atomic_open(self._paths.config_file) as handle:
                self._parser.write(handle)
        except OSError as exc:
            log.error("Could not save config: %s", exc)

    # -- Qt geometry -------------------------------------------------------
    def save_geometry(self, window: QtWidgets.QMainWindow) -> None:
        try:
            self._parser.set(self.SECTION, "geometry",
                             bytes(window.saveGeometry().toBase64()).decode("ascii"))
            self._parser.set(self.SECTION, "window_state",
                             bytes(window.saveState().toBase64()).decode("ascii"))
            self.save()
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not persist geometry: %s", exc)

    def restore_geometry(self, window: QtWidgets.QMainWindow) -> None:
        for key, restore in (("geometry", window.restoreGeometry),
                             ("window_state", window.restoreState)):
            blob = self.get(key, "")
            if not blob:
                continue
            try:
                restore(QtCore.QByteArray.fromBase64(blob.encode("ascii")))
            except Exception as exc:  # noqa: BLE001
                log.debug("Could not restore %s: %s", key, exc)

    # -- legacy bridge -----------------------------------------------------
    def local_library(self) -> str:
        """User's local SD-card mirror, from Wolfpole or Tadpole settings."""
        own = self.get("local_library", "")
        if own:
            return own
        if self.legacy is not None:
            getter = getattr(self.legacy, "getLocalUserDirectory", None)
            if callable(getter):
                try:
                    value = getter()
                    if value and value != getattr(
                        self.legacy, "_static_general_userDirectory_DEFAULT", None
                    ):
                        return str(value)
                except Exception as exc:  # noqa: BLE001
                    log.debug("getLocalUserDirectory failed: %s", exc)
        return ""

    def __getattr__(self, item: str) -> Any:
        """Delegate unknown attributes to TadpoleConfig for legacy dialogs."""
        legacy = self.__dict__.get("legacy")
        if legacy is not None and hasattr(legacy, item):
            return getattr(legacy, item)
        raise AttributeError(item)


# ---------------------------------------------------------------------------
# Safe filesystem helpers
# ---------------------------------------------------------------------------


class atomic_open:
    """Context manager writing to ``path`` via a temp file + replace.

    A half-written ini or index file on an SD card is one of the ways SF2000
    users end up with a device that will not boot.
    """

    def __init__(self, path: Path, mode: str = "w", encoding: str | None = "utf-8") -> None:
        self.path = Path(path)
        self.tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.mode = mode
        self.encoding = None if "b" in mode else encoding
        self.handle = None

    def __enter__(self):
        self.tmp.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.tmp, self.mode, encoding=self.encoding)
        return self.handle

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self.handle is not None
        self.handle.flush()
        try:
            os.fsync(self.handle.fileno())
        except OSError:
            pass  # not all filesystems support fsync
        self.handle.close()
        if exc_type is None:
            os.replace(self.tmp, self.path)
        else:
            self.tmp.unlink(missing_ok=True)
        return False


def is_protected(path: str | os.PathLike[str]) -> bool:
    """True for roots we never format, wipe or bulk-overwrite."""
    try:
        resolved = str(Path(path).resolve()).lower().rstrip("\\/") or "/"
    except OSError:
        return True
    candidates = {resolved, resolved + "\\", resolved + "/"}
    return bool(candidates & PROTECTED_ROOTS)


def human_size(num_bytes: float) -> str:
    """1536 -> '1.5 KB'. Binary units, one decimal, never scientific."""
    if num_bytes is None:
        return "-"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def free_space(path: str | os.PathLike[str]) -> tuple[int, int]:
    """(free, total) bytes, or (0, 0) if the path is not readable."""
    try:
        usage = shutil.disk_usage(str(path))
        return usage.free, usage.total
    except OSError:
        return 0, 0


def sha256_file(path: str | os.PathLike[str], chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def open_in_file_manager(path: str | os.PathLike[str]) -> None:
    """Reveal a file or folder in Explorer / Finder / the desktop file manager."""
    target = Path(path)
    folder = target if target.is_dir() else target.parent
    url = QtCore.QUrl.fromLocalFile(str(folder))
    QtGui.QDesktopServices.openUrl(url)


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


class Cancelled(Exception):
    """Raised inside a job body when the user cancels."""


class JobContext:
    """Handed to job functions: progress reporting plus cooperative cancel."""

    def __init__(self, signals: "JobSignals") -> None:
        self._signals = signals
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def check(self) -> None:
        if self._cancelled:
            raise Cancelled()

    def progress(self, value: int, total: int = 0, text: str = "") -> None:
        self.check()
        self._signals.progress.emit(int(value), int(total), str(text))

    def status(self, text: str) -> None:
        self._signals.message.emit(str(text))


class JobSignals(QtCore.QObject):
    progress = Signal(int, int, str)
    message = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    done = Signal()


class Job(QtCore.QRunnable):
    """Runs ``fn(ctx, *args, **kwargs)`` on the thread pool.

    Every long operation in Madpole ran on the GUI thread, which is why the
    window greyed out during rebuilds and froze for seconds at startup while
    three JSON catalogues downloaded. Here the GUI thread only ever handles
    signals.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.signals = JobSignals()
        self.ctx = JobContext(self.signals)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self.ctx.cancel()

    @Slot()
    def run(self) -> None:  # pragma: no cover - thread entry point
        try:
            result = self._fn(self.ctx, *self._args, **self._kwargs)
        except Cancelled:
            log.info("Job cancelled: %s", getattr(self._fn, "__name__", self._fn))
            self.signals.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            log.exception("Job failed: %s", getattr(self._fn, "__name__", self._fn))
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(result)
        finally:
            self.signals.done.emit()


class JobRunner(QtCore.QObject):
    """Owns the thread pool and keeps references so jobs are not collected."""

    def __init__(self, parent: QtCore.QObject | None = None, max_threads: int = 4) -> None:
        super().__init__(parent)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(max_threads, os.cpu_count() or 4)))
        self._live: list[Job] = []

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_result: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_message: Callable[[str], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        on_done: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> Job:
        job = Job(fn, *args, **kwargs)
        if on_result:
            job.signals.finished.connect(on_result, QUEUED)
        if on_error:
            job.signals.failed.connect(on_error, QUEUED)
        if on_progress:
            job.signals.progress.connect(on_progress, QUEUED)
        if on_message:
            job.signals.message.connect(on_message, QUEUED)
        if on_cancelled:
            job.signals.cancelled.connect(on_cancelled, QUEUED)

        self._live.append(job)

        def _cleanup() -> None:
            if job in self._live:
                self._live.remove(job)
            if on_done:
                on_done()

        job.signals.done.connect(_cleanup, QUEUED)
        self.pool.start(job)
        return job

    def cancel_all(self) -> None:
        for job in list(self._live):
            job.cancel()

    def wait(self, msecs: int = 3000) -> None:
        self.cancel_all()
        self.pool.waitForDone(msecs)


# ---------------------------------------------------------------------------
# Drives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveInfo:
    mountpoint: str
    label: str
    froggy: bool
    free: int = 0
    total: int = 0
    local: bool = False

    @property
    def key(self) -> str:
        return self.mountpoint

    def describe(self) -> str:
        if self.total:
            return f"{self.label}  —  {human_size(self.free)} free of {human_size(self.total)}"
        return self.label


def looks_froggy(mountpoint: str) -> bool:
    """Does this volume look like an SF2000 card?

    Prefers tadpole_functions' own check so behaviour stays consistent with
    the rest of the toolchain, with a structural fallback.
    """
    tadpole_functions = optional_import("tadpole_functions")
    checker = getattr(tadpole_functions, "checkDriveLooksFroggy", None) if tadpole_functions else None
    if callable(checker):
        try:
            return bool(checker(mountpoint))
        except Exception:  # noqa: BLE001 - unreadable media
            return False
    root = Path(mountpoint)
    try:
        return (root / "bios" / "bisrv.asd").is_file() and (root / "Resources").is_dir()
    except OSError:
        return False


def scan_drives(local_library: str = "") -> list[DriveInfo]:
    """Enumerate candidate drives. Safe to call from a worker thread."""
    found: list[DriveInfo] = []

    if local_library and Path(local_library).is_dir():
        free, total = free_space(local_library)
        found.append(DriveInfo(str(local_library), f"Local library ({local_library})",
                               looks_froggy(local_library), free, total, local=True))

    psutil = optional_import("psutil")
    partitions: Iterable[Any] = []
    if psutil is not None:
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("disk_partitions failed: %s", exc)

    for partition in partitions:
        mount = getattr(partition, "mountpoint", "")
        if not mount or not looks_froggy(mount):
            continue
        free, total = free_space(mount)
        found.append(DriveInfo(mount, f"SF2000 card ({mount})", True, free, total))
    return found


class DriveWatcher(QtCore.QThread):
    """Polls for SF2000 cards off the GUI thread.

    Madpole polled every second on the GUI thread and rebuilt the drive
    combo box each tick, which dropped the user's selection, re-fired change
    handlers, and stuttered whenever a drive was slow to answer. This thread
    hashes the result and emits only on a genuine change.
    """

    drivesChanged = Signal(list)

    def __init__(self, config: Config, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._stop = False
        self._signature: tuple[Any, ...] = ()

    def stop(self) -> None:
        self._stop = True

    def poke(self) -> None:
        """Force the next poll to emit, e.g. after we changed the card."""
        self._signature = ()

    def run(self) -> None:  # pragma: no cover - thread entry point
        while not self._stop:
            try:
                drives = scan_drives(self._config.local_library())
                signature = tuple((d.mountpoint, d.froggy) for d in drives)
                if signature != self._signature:
                    self._signature = signature
                    self.drivesChanged.emit(drives)
            except Exception as exc:  # noqa: BLE001 - never kill the watcher
                log.debug("Drive scan error: %s", exc)
            interval = max(1, self._config.get_int("poll_seconds", 2))
            for _ in range(interval * 10):
                if self._stop:
                    return
                self.msleep(100)


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------


def decode_thumbnail(path: str | os.PathLike[str]) -> QtGui.QImage | None:
    """Decode the RGB565 thumbnail embedded in a .zxx/.zfb header.

    Two bugs in the original are fixed here:

    1. ``QImage`` does not copy the buffer it is given. Madpole built the image
       over a local ``bytearray`` that was then garbage-collected, leaving the
       image pointing at freed memory. We ``.copy()`` before returning.
    2. Decoding happened on the GUI thread for every row. This function is
       thread-safe (QImage is; QPixmap is not) so it runs in the pool.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read(THUMB_BYTES)
    except OSError as exc:
        log.debug("Thumbnail read failed for %s: %s", path, exc)
        return None
    if len(raw) < THUMB_BYTES:
        return None
    image = QtGui.QImage(raw, THUMB_W, THUMB_H, THUMB_W * 2, FORMAT_RGB16)
    if image.isNull():
        return None
    return image.copy()  # detach from the temporary buffer


class ThumbnailCache(QtCore.QObject):
    """mtime-keyed LRU of decoded thumbnails, filled by background jobs.

    The model asks for a pixmap; if it is not cached the cache schedules a
    decode and emits :attr:`ready` when it lands, so scrolling a 900-ROM
    folder never blocks.
    """

    ready = Signal(str)

    def __init__(self, runner: JobRunner, capacity: int = 600,
                 parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._capacity = capacity
        self._images: "OrderedDict[str, QtGui.QImage | None]" = OrderedDict()
        self._pixmaps: "OrderedDict[str, QtGui.QPixmap | None]" = OrderedDict()
        self._pending: set[str] = set()

    @staticmethod
    def _key(path: str) -> str:
        try:
            stat = os.stat(path)
            return f"{path}|{int(stat.st_mtime)}|{stat.st_size}"
        except OSError:
            return f"{path}|missing"

    def clear(self) -> None:
        self._images.clear()
        self._pixmaps.clear()
        self._pending.clear()

    def pixmap(self, path: str) -> QtGui.QPixmap | None:
        """Cached pixmap, or None while a decode is scheduled.

        Must be called from the GUI thread: QPixmap construction is not
        thread-safe, so workers only ever produce QImages.
        """
        key = self._key(path)
        if key in self._pixmaps:
            self._pixmaps.move_to_end(key)
            return self._pixmaps[key]
        if key in self._images:
            image = self._images.pop(key)
            pixmap = QtGui.QPixmap.fromImage(image) if image is not None else None
            self._store(self._pixmaps, key, pixmap)
            return pixmap
        if key not in self._pending:
            self._pending.add(key)
            self._runner.submit(
                lambda ctx, p=path: decode_thumbnail(p),
                on_result=lambda image, p=path, k=key: self._received(k, p, image),
                on_error=lambda msg, k=key: self._pending.discard(k),
            )
        return None

    def _received(self, key: str, path: str, image: QtGui.QImage | None) -> None:
        self._pending.discard(key)
        self._store(self._images, key, image)
        self.ready.emit(path)

    def _store(self, store: OrderedDict, key: str, value: Any) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > self._capacity:
            store.popitem(last=False)


# ---------------------------------------------------------------------------
# ROM scanning and model
# ---------------------------------------------------------------------------


@dataclass
class RomEntry:
    path: str
    name: str
    size: int
    ext: str
    system: str
    mtime: float = 0.0
    core: str = ""
    core_checked: bool = False
    slot: int = 0  # 0 = no shortcut, 1..4 = slot

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.filename)[0]

    @property
    def has_thumbnail(self) -> bool:
        return self.ext in {"." + zxx_ext_for(self.system), ".zfb"} and self.size > THUMB_BYTES


def rom_title(path: str, system: str) -> str:
    """Display title for a ROM, preferring the project's sf2000ROM parser."""
    rom_cls = optional_import("sf2000ROM", "sf2000ROM")
    if rom_cls is not None:
        try:
            obj = rom_cls(path)
            title = getattr(obj, "title", "") or ""
            if title:
                return str(title)
        except Exception as exc:  # noqa: BLE001
            log.debug("sf2000ROM failed for %s: %s", path, exc)
    return os.path.splitext(os.path.basename(path))[0]


def detect_core(path: str) -> str:
    """Multicore core name for a .zfb stub, or '' when not multicore."""
    if not path.lower().endswith(".zfb"):
        return ""
    mc = optional_import("mcoredata")
    getter = getattr(mc, "getZfbCore", None) if mc else None
    if not callable(getter):
        return ""
    try:
        return str(getter(path) or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("getZfbCore failed for %s: %s", path, exc)
        return ""


def scan_roms(ctx: JobContext, drive: str, system: str) -> list[RomEntry]:
    """Scan one system folder. Runs in a worker thread."""
    folder = Path(drive) / system
    if not folder.is_dir():
        return []

    ctx.status(f"Scanning {system}…")
    try:
        entries = [e for e in os.scandir(folder) if e.is_file()]
    except OSError as exc:
        raise RuntimeError(f"Cannot read {folder}: {exc}") from exc

    index_names = {"xfgle.hqk"}
    roms: list[RomEntry] = []
    total = len(entries)
    for i, entry in enumerate(entries):
        if i % 64 == 0:
            ctx.progress(i, total, f"Scanning {system}… {i}/{total}")
        name = entry.name
        lower = name.lower()
        if lower in index_names or lower.endswith((".tmp", ".bak", ".ini", ".hqk", ".db")):
            continue
        ext = os.path.splitext(lower)[1]
        if ext and ext not in ROM_EXTENSIONS:
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        roms.append(
            RomEntry(
                path=entry.path,
                name=rom_title(entry.path, system),
                size=stat.st_size,
                ext=ext,
                system=system,
                mtime=stat.st_mtime,
            )
        )
    roms.sort(key=lambda r: r.name.lower())
    ctx.progress(total, total, f"{len(roms)} ROMs in {system}")
    return roms


#: Tags ROM filenames collect from dump sites, stripped by tidy_title().
_TAG_OPENERS = {"(": ")", "[": "]", "{": "}"}


def tidy_title(name: str) -> str:
    """Turn ``Super_Mario_World_(USA)_[!].sfc`` into ``Super Mario World``.

    Region tags and dump flags are what make a cover grid unreadable, and the
    SF2000's own title display is narrow, so cleaning them up is worth doing in
    bulk rather than one rename at a time.
    """
    stem = os.path.splitext(name)[0]
    out: list[str] = []
    depth = 0
    closer = ""
    for char in stem:
        if depth == 0 and char in _TAG_OPENERS:
            depth = 1
            closer = _TAG_OPENERS[char]
            continue
        if depth and char == closer:
            depth = 0
            continue
        if depth:
            continue
        out.append(char)
    cleaned = "".join(out).replace("_", " ").replace(".", " ")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip(" -–—,")
    # "Legend of Zelda, The" reads better the way everyone says it.
    for article in ("The", "A", "An"):
        suffix = f", {article}"
        if cleaned.endswith(suffix):
            cleaned = f"{article} {cleaned[: -len(suffix)]}"
            break
    return cleaned or stem


def scan_all_systems(ctx: JobContext, drive: str) -> list[RomEntry]:
    """Every ROM on the card, for cross-system search and duplicate hunting."""
    everything: list[RomEntry] = []
    systems = known_systems()
    for index, system in enumerate(systems):
        ctx.check()
        ctx.progress(index, len(systems), f"Scanning {system}…")
        try:
            everything.extend(scan_roms(ctx, drive, system))
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad folder must not stop the rest
            log.warning("Scan failed for %s: %s", system, exc)
    everything.sort(key=lambda rom: rom.name.lower())
    ctx.progress(len(systems), len(systems), f"{len(everything)} ROMs")
    return everything


def find_duplicates(roms: Iterable[RomEntry]) -> list[list[RomEntry]]:
    """Group ROMs that are probably the same game.

    Matches on the tidied title plus file size, which catches the same dump
    copied into two systems as well as ``Game (USA).zfc`` sitting beside
    ``Game.zfc``.
    """
    groups: dict[tuple[str, int], list[RomEntry]] = {}
    for rom in roms:
        key = (tidy_title(rom.filename).lower(), rom.size)
        groups.setdefault(key, []).append(rom)
    duplicates = [group for group in groups.values() if len(group) > 1]
    duplicates.sort(key=lambda group: -sum(rom.size for rom in group))
    return duplicates


def count_roms(ctx: JobContext, drive: str) -> dict[str, int]:
    """How many ROMs sit in each system folder. Runs in a worker."""
    counts: dict[str, int] = {}
    for system in known_systems():
        ctx.check()
        folder = Path(drive) / system
        if not folder.is_dir():
            counts[system] = 0
            continue
        total = 0
        try:
            for entry in os.scandir(folder):
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name.lower())[1]
                if ext in ROM_EXTENSIONS:
                    total += 1
        except OSError:
            total = 0
        counts[system] = total
    return counts


class ShortcutStore:
    """Reads and writes ``mshortcuts.ini``, the multicore shortcut slot map.

    The original created the file, rewrote it once per section inside the loop,
    and only seeded defaults when a section was missing entirely -- so a file
    with a section but no keys stayed broken. This repairs and writes once.
    """

    SECTIONS = ("ARCADE", "FC", "SFC", "GB", "GBC", "GBA", "MD")

    def __init__(self, folder: str) -> None:
        self.path = Path(folder) / "mshortcuts.ini"
        self.parser = configparser.ConfigParser()
        try:
            self.parser.read(self.path, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - a corrupt ini is not fatal
            log.warning("Could not read %s: %s", self.path, exc)

        dirty = False
        for section in self.SECTIONS:
            if not self.parser.has_section(section):
                self.parser.add_section(section)
                dirty = True
            for slot in "1234":
                if not self.parser.has_option(section, slot):
                    self.parser.set(section, slot, "")
                    dirty = True
        if dirty:
            self.save()

    def save(self) -> None:
        try:
            with atomic_open(self.path) as handle:
                self.parser.write(handle)
        except OSError as exc:
            log.error("Could not write %s: %s", self.path, exc)

    def slots(self, system: str) -> dict[str, int]:
        """{filename: slot} for one system."""
        if not self.parser.has_section(system):
            return {}
        return {
            filename: int(slot)
            for slot, filename in self.parser.items(system)
            if filename and slot.isdigit()
        }

    def filename_for(self, system: str, slot: int) -> str:
        return self.parser.get(system, str(slot), fallback="")

    def set_slot(self, system: str, slot: int, filename: str) -> None:
        if not self.parser.has_section(system):
            self.parser.add_section(system)
        if filename:
            for existing in "1234":
                if self.parser.get(system, existing, fallback="") == filename:
                    self.parser.set(system, existing, "")
        if 1 <= slot <= 4:
            self.parser.set(system, str(slot), filename)
        self.save()


class RomModel(QtCore.QAbstractTableModel):
    """Table model over a list of :class:`RomEntry`.

    Madpole created five widgets per row — including a live QComboBox — and
    decoded a thumbnail inline, so opening a large folder took seconds and
    ~5x the memory. A model only renders what is on screen.
    """

    COL_THUMB, COL_NAME, COL_SIZE, COL_FORMAT, COL_CORE, COL_SLOT = range(6)
    HEADERS = ["Thumbnail", "Name", "Size", "Format", "Core", "Shortcut"]

    slotChanged = Signal(object, int)  # RomEntry, new slot
    nameChanged = Signal(object, str)  # RomEntry, new name

    def __init__(self, cache: ThumbnailCache, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._roms: list[RomEntry] = []
        self._cache = cache
        self._show_thumbs = True
        self._thumb_height = 104
        self._cache.ready.connect(self._thumbnail_ready)

    # -- population --------------------------------------------------------
    def set_roms(self, roms: list[RomEntry]) -> None:
        self.beginResetModel()
        self._roms = roms
        self.endResetModel()

    def roms(self) -> list[RomEntry]:
        return self._roms

    def rom_at(self, row: int) -> RomEntry | None:
        if 0 <= row < len(self._roms):
            return self._roms[row]
        return None

    def remove_rows(self, rows: Iterable[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self._roms):
                self.beginRemoveRows(QtCore.QModelIndex(), row, row)
                del self._roms[row]
                self.endRemoveRows()

    # -- display options ---------------------------------------------------
    def set_show_thumbnails(self, show: bool) -> None:
        if show != self._show_thumbs:
            self.beginResetModel()
            self._show_thumbs = show
            self.endResetModel()

    def show_thumbnails(self) -> bool:
        return self._show_thumbs

    def set_thumb_height(self, height: int) -> None:
        self._thumb_height = max(32, int(height))
        if self._roms:
            self.dataChanged.emit(
                self.index(0, self.COL_THUMB),
                self.index(len(self._roms) - 1, self.COL_THUMB),
                [ROLE_DECORATION],
            )

    def thumb_size(self) -> QtCore.QSize:
        width = int(self._thumb_height * THUMB_W / THUMB_H)
        return QtCore.QSize(width, self._thumb_height)

    # -- QAbstractTableModel ----------------------------------------------
    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._roms)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation, role=ROLE_DISPLAY):
        if role != ROLE_DISPLAY or orientation != ORIENT_HORIZONTAL:
            return None
        return self.HEADERS[section]

    def flags(self, index: QtCore.QModelIndex):
        if not index.isValid():
            return FLAG_ENABLED
        base = FLAG_ENABLED | FLAG_SELECTABLE
        if index.column() in (self.COL_NAME, self.COL_SLOT):
            base |= FLAG_EDITABLE
        return base

    def data(self, index: QtCore.QModelIndex, role=ROLE_DISPLAY):
        if not index.isValid():
            return None
        rom = self._roms[index.row()]
        column = index.column()

        if role in (ROLE_DISPLAY, ROLE_EDIT):
            if column == self.COL_NAME:
                return rom.name
            if column == self.COL_SIZE:
                return human_size(rom.size)
            if column == self.COL_FORMAT:
                return rom.ext.lstrip(".").upper() or "—"
            if column == self.COL_CORE:
                if not rom.core_checked:
                    rom.core = detect_core(rom.path)
                    rom.core_checked = True
                return rom.core or ("stock" if rom.ext != ".zfb" else "—")
            if column == self.COL_SLOT:
                return str(rom.slot) if rom.slot else ""
            if column == self.COL_THUMB and not self._show_thumbs:
                return "view"
            return None

        if role == ROLE_DECORATION and column == self.COL_THUMB and self._show_thumbs:
            if not rom.has_thumbnail:
                return None
            pixmap = self._cache.pixmap(rom.path)
            if pixmap is None or pixmap.isNull():
                return None
            return pixmap.scaled(self.thumb_size(), KEEP_ASPECT, SMOOTH_TRANSFORM)

        if role == ROLE_SORT:
            if column == self.COL_SIZE:
                return rom.size
            if column == self.COL_NAME:
                return rom.name.lower()
            if column == self.COL_SLOT:
                return rom.slot
            if column == self.COL_CORE:
                return (rom.core or "").lower()
            if column == self.COL_FORMAT:
                return rom.ext
            return 0

        if role == ROLE_FILTER:
            return f"{rom.name}\n{rom.filename}\n{rom.ext}\n{rom.core}".lower()

        if role == ROLE_TEXT_ALIGN:
            if column in (self.COL_SIZE, self.COL_FORMAT, self.COL_SLOT, self.COL_CORE):
                return int(ALIGN_CENTER)
            return int(ALIGN_LEFT_VCENTER)

        if role == ROLE_TOOLTIP:
            return (
                f"{rom.name}\n{rom.path}\n{human_size(rom.size)}"
                f"\nModified {datetime.fromtimestamp(rom.mtime):%Y-%m-%d %H:%M}"
            )

        if role == ROLE_USER:
            return rom
        return None

    def setData(self, index: QtCore.QModelIndex, value: Any, role=ROLE_EDIT) -> bool:
        if not index.isValid() or role != ROLE_EDIT:
            return False
        rom = self._roms[index.row()]
        if index.column() == self.COL_SLOT:
            try:
                slot = int(value) if str(value).strip() else 0
            except (TypeError, ValueError):
                return False
            if slot == rom.slot:
                return True
            rom.slot = slot
            self.dataChanged.emit(index, index, [ROLE_DISPLAY])
            self.slotChanged.emit(rom, slot)
            return True
        if index.column() == self.COL_NAME:
            text = str(value).strip()
            if not text or text == rom.name:
                return False
            self.nameChanged.emit(rom, text)
            return True
        return False

    def _thumbnail_ready(self, path: str) -> None:
        for row, rom in enumerate(self._roms):
            if rom.path == path:
                self.dataChanged.emit(
                    self.index(row, 0),
                    self.index(row, self.columnCount() - 1),
                    [ROLE_DECORATION],
                )
                return


class RomFilterProxy(QtCore.QSortFilterProxyModel):
    """Live search across name, filename, extension and core."""

    MODES = ("all", "no_art", "shortcuts", "multicore", "large")

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._terms: list[str] = []
        self._only_missing_thumbs = False
        self._mode = "all"
        self.setDynamicSortFilter(True)

    def set_mode(self, mode: str) -> None:
        self._mode = mode if mode in self.MODES else "all"
        self.invalidateFilter()

    def mode(self) -> str:
        return self._mode

    def _mode_accepts(self, rom: "RomEntry") -> bool:
        if self._mode == "no_art":
            return not rom.has_thumbnail
        if self._mode == "shortcuts":
            return bool(rom.slot)
        if self._mode == "multicore":
            return rom.ext == ".zfb"
        if self._mode == "large":
            return rom.size >= 8 * 1024 * 1024
        return True

    def set_query(self, text: str) -> None:
        self._terms = [t for t in text.lower().split() if t]
        self.invalidateFilter()

    def set_only_missing_thumbnails(self, enabled: bool) -> None:
        self._only_missing_thumbs = enabled
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QtCore.QModelIndex) -> bool:
        model = self.sourceModel()
        index = model.index(row, 0, parent)
        rom = model.data(index, ROLE_USER)
        if rom is not None and not self._mode_accepts(rom):
            return False
        if self._only_missing_thumbs and rom is not None and rom.has_thumbnail:
            return False
        if not self._terms:
            return True
        haystack = model.data(index, ROLE_FILTER) or ""
        return all(term in haystack for term in self._terms)

    def lessThan(self, left: QtCore.QModelIndex, right: QtCore.QModelIndex) -> bool:
        model = self.sourceModel()
        a = model.data(left, ROLE_SORT)
        b = model.data(right, ROLE_SORT)
        if a is None or b is None:
            return str(a) < str(b)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class SlotDelegate(QtWidgets.QStyledItemDelegate):
    """Combo-box editor for the shortcut column.

    Madpole embedded a live QComboBox in every row and wired a signal to each;
    a delegate creates exactly one editor, only while editing.
    """

    CHOICES = ["", "1", "2", "3", "4"]

    def createEditor(self, parent, option, index):
        combo = QtWidgets.QComboBox(parent)
        combo.addItems(self.CHOICES)
        return combo

    def setEditorData(self, editor: QtWidgets.QComboBox, index) -> None:
        current = str(index.data(ROLE_EDIT) or "")
        position = self.CHOICES.index(current) if current in self.CHOICES else 0
        editor.setCurrentIndex(position)

    def setModelData(self, editor: QtWidgets.QComboBox, model, index) -> None:
        model.setData(index, editor.currentText(), ROLE_EDIT)


# ---------------------------------------------------------------------------
# Trash / undo
# ---------------------------------------------------------------------------


class Trash:
    """Deletions move here first, so 'Undo delete' is real.

    Madpole called ``os.remove`` straight from a confirmation dialog. On a
    device where a mis-click costs you a ROM and its thumbnail, that is worth
    fixing.
    """

    INDEX_NAME = "index.json"

    def __init__(self, paths: AppPaths) -> None:
        self.root = paths.trash_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / self.INDEX_NAME

    def _load(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        try:
            with atomic_open(self.index_path) as handle:
                json.dump(entries, handle, indent=1)
        except OSError as exc:
            log.error("Could not write trash index: %s", exc)

    def put(self, path: str) -> bool:
        source = Path(path)
        if not source.exists():
            return False
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.root / f"{stamp}__{source.name}"
        try:
            shutil.move(str(source), str(target))
        except (OSError, shutil.Error) as exc:
            log.error("Could not move %s to trash: %s", source, exc)
            return False
        entries = self._load()
        entries.append({"original": str(source), "stored": str(target), "when": stamp})
        self._save(entries)
        return True

    def last_batch(self, count: int) -> list[dict[str, Any]]:
        return self._load()[-count:] if count > 0 else []

    def restore(self, entries: list[dict[str, Any]]) -> int:
        index = self._load()
        restored = 0
        for entry in entries:
            stored = Path(entry.get("stored", ""))
            original = Path(entry.get("original", ""))
            if not stored.exists() or not original.parent.is_dir():
                continue
            try:
                shutil.move(str(stored), str(original))
                restored += 1
                index = [e for e in index if e.get("stored") != str(stored)]
            except (OSError, shutil.Error) as exc:
                log.error("Could not restore %s: %s", stored, exc)
        self._save(index)
        return restored

    def size(self) -> int:
        total = 0
        for item in self.root.glob("*"):
            if item.is_file() and item.name != self.INDEX_NAME:
                total += item.stat().st_size
        return total

    def empty(self) -> None:
        for item in self.root.glob("*"):
            try:
                if item.is_file():
                    item.unlink()
                else:
                    shutil.rmtree(item, ignore_errors=True)
            except OSError:
                pass
        self._save([])


# ---------------------------------------------------------------------------
# Remote catalogues (firmware / themes / music / boot logos)
# ---------------------------------------------------------------------------


class Catalogues:
    """Fetches the online resource lists, with an on-disk cache.

    Madpole downloaded three catalogues synchronously inside ``loadMenus()``,
    so with no internet the window took the full TCP timeout to appear. Here
    every fetch is a background job and the last good result is cached, so the
    menus populate instantly and offline.
    """

    OS_JSON = "https://tadpolestorage.blob.core.windows.net/$web/os.json"
    TTL_SECONDS = 24 * 3600

    def __init__(self, paths: AppPaths, config: Config) -> None:
        self._paths = paths
        self._config = config
        self.cache_dir = paths.cache_dir

    # -- cache -------------------------------------------------------------
    def _cache_file(self, name: str) -> Path:
        return self._paths.cache_dir / f"{name}.json"

    def cached(self, name: str, max_age: int | None = None) -> Any | None:
        path = self._cache_file(name)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if max_age is not None and age > max_age:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def store(self, name: str, payload: Any) -> None:
        try:
            with atomic_open(self._cache_file(name)) as handle:
                json.dump(payload, handle)
        except OSError as exc:
            log.debug("Could not cache %s: %s", name, exc)

    @property
    def online(self) -> bool:
        return self._config.get_bool("network")

    # -- fetchers (run inside jobs) ---------------------------------------
    def fetch_firmware(self, ctx: JobContext) -> dict[str, Any]:
        cached = self.cached("firmware", self.TTL_SECONDS)
        if cached is not None and not ctx.cancelled:
            return cached
        if not self.online:
            return self.cached("firmware") or {"official": [], "multicore": [], "latest": None}

        requests = optional_import("requests")
        if requests is None:
            return self.cached("firmware") or {"official": [], "multicore": [], "latest": None}

        ctx.status("Fetching firmware list…")
        response = requests.get(self.OS_JSON, timeout=15)
        response.raise_for_status()
        data = response.json()
        result = {
            "official": [
                {"title": item["title"], "link": item["link"]}
                for item in data.get("official", {}).get("versions", [])
            ],
            "multicore": [
                {"title": item["title"], "link": item["link"]}
                for item in data.get("multicore", {}).get("versions", [])
            ],
            "latest": data.get("multicore", {}).get("latest"),
        }
        self.store("firmware", result)
        return result

    def _fetch_named(self, ctx: JobContext, name: str, getter_name: str) -> dict[str, str]:
        cached = self.cached(name, self.TTL_SECONDS)
        if cached is not None and not ctx.cancelled:
            return cached
        if not self.online:
            return self.cached(name) or {}

        tadpole_functions = optional_import("tadpole_functions")
        getter = getattr(tadpole_functions, getter_name, None) if tadpole_functions else None
        if not callable(getter):
            return self.cached(name) or {}

        ctx.status(f"Fetching {name}…")
        result = dict(getter() or {})
        self.store(name, result)
        return result

    def fetch_themes(self, ctx: JobContext) -> dict[str, str]:
        return self._fetch_named(ctx, "themes", "get_themes")

    def fetch_music(self, ctx: JobContext) -> dict[str, str]:
        return self._fetch_named(ctx, "music", "get_background_music")

    def fetch_bootlogos(self, ctx: JobContext) -> dict[str, str]:
        return self._fetch_named(ctx, "bootlogos", "get_boot_logos")

    def fetch_thumbnail_index(self, ctx: JobContext, system: str) -> list[str]:
        """List available box-art filenames for a system.

        Madpole scraped GitHub's HTML with BeautifulSoup and dug a JSON blob
        out of the first tag — which breaks whenever GitHub touches its
        markup. This uses the documented contents API instead.
        """
        folders = {
            "FC": "Nintendo - Nintendo Entertainment System",
            "SFC": "Nintendo - Super Nintendo Entertainment System",
            "MD": "Sega - Mega Drive - Genesis",
            "GB": "Nintendo - Game Boy",
            "GBC": "Nintendo - Game Boy Color",
            "GBA": "Nintendo - Game Boy Advance",
        }
        folder = folders.get(system)
        if not folder:
            return []

        name = f"art-{system}"
        cached = self.cached(name, self.TTL_SECONDS)
        if cached is not None:
            return cached
        if not self.online:
            return self.cached(name) or []

        requests = optional_import("requests")
        if requests is None:
            return []

        ctx.status(f"Looking up box art for {system}…")
        url = (
            "https://api.github.com/repos/EricGoldsteinNz/libretro-thumbnails/"
            f"contents/{folder}/Named_Snaps"
        )
        response = requests.get(
            url,
            timeout=30,
            headers={"Accept": "application/vnd.github+json"},
            params={"ref": "master"},
        )
        response.raise_for_status()
        names = [
            item["name"]
            for item in response.json()
            if item.get("type") == "file"
            and item["name"].lower().endswith((".png", ".jpg", ".jpeg"))
        ]
        self.store(name, names)
        return names

    @staticmethod
    def art_url(system: str, filename: str) -> str:
        folders = {
            "FC": "Nintendo - Nintendo Entertainment System",
            "SFC": "Nintendo - Super Nintendo Entertainment System",
            "MD": "Sega - Mega Drive - Genesis",
            "GB": "Nintendo - Game Boy",
            "GBC": "Nintendo - Game Boy Color",
            "GBA": "Nintendo - Game Boy Advance",
        }
        from urllib.parse import quote

        folder = quote(folders.get(system, ""))
        return (
            "https://raw.githubusercontent.com/EricGoldsteinNz/libretro-thumbnails/"
            f"master/{folder}/Named_Snaps/{quote(filename)}"
        )


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

_PALETTES = {
    "dark": {
        "bg": "#14161a", "panel": "#1c1f26", "panel2": "#232833", "line": "#2e3440",
        "text": "#e6e9ef", "dim": "#98a1b3", "accent": "#e8973a", "accent_dim": "#b8732a",
        "sel": "#2c3545", "danger": "#e2574c", "ok": "#4caf7d",
    },
    "light": {
        "bg": "#f4f5f7", "panel": "#ffffff", "panel2": "#eef0f4", "line": "#d6dae1",
        "text": "#1b1f27", "dim": "#5d6675", "accent": "#c96a10", "accent_dim": "#a5560b",
        "sel": "#dce6f5", "danger": "#c0392b", "ok": "#2e8b57",
    },
}

_QSS_TEMPLATE = """
* {{ outline: none; }}
QWidget {{
    background: {bg};
    color: {text};
    font-size: 13px;
}}
QMainWindow::separator {{ background: {line}; width: 1px; height: 1px; }}

QMenuBar {{ background: {panel}; border-bottom: 1px solid {line}; padding: 2px; }}
QMenuBar::item {{ padding: 5px 11px; border-radius: 5px; background: transparent; }}
QMenuBar::item:selected {{ background: {panel2}; }}
QMenu {{ background: {panel}; border: 1px solid {line}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {sel}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 5px 8px; }}

QToolBar {{
    background: {panel};
    border: none;
    border-bottom: 1px solid {line};
    padding: 6px 8px;
    spacing: 6px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 6px 10px;
    color: {text};
}}
QToolButton:hover {{ background: {panel2}; border-color: {line}; }}
QToolButton:pressed {{ background: {sel}; }}
QToolButton:disabled {{ color: {dim}; }}

QPushButton {{
    background: {panel2};
    border: 1px solid {line};
    border-radius: 7px;
    padding: 6px 14px;
    min-height: 18px;
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:pressed {{ background: {sel}; }}
QPushButton:disabled {{ color: {dim}; background: {panel}; }}
QPushButton#primary {{
    background: {accent}; border-color: {accent}; color: #1a1205; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {accent_dim}; }}
QPushButton#danger {{ border-color: {danger}; color: {danger}; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {panel};
    border: 1px solid {line};
    border-radius: 7px;
    padding: 5px 9px;
    selection-background-color: {accent};
    selection-color: #1a1205;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {panel};
    border: 1px solid {line};
    selection-background-color: {sel};
    padding: 4px;
}}

QTableView {{
    background: {panel};
    alternate-background-color: {panel2};
    border: 1px solid {line};
    border-radius: 9px;
    gridline-color: {line};
    selection-background-color: {sel};
    selection-color: {text};
}}
QTableView::item {{ padding: 6px; border: none; }}
QHeaderView::section {{
    background: {panel2};
    color: {dim};
    border: none;
    border-right: 1px solid {line};
    border-bottom: 1px solid {line};
    padding: 7px 8px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {text}; }}
QTableCornerButton::section {{ background: {panel2}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: {line}; border-radius: 5px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {panel}; border-top: 1px solid {line}; color: {dim}; }}
QStatusBar::item {{ border: none; }}

QProgressBar {{
    background: {panel2};
    border: 1px solid {line};
    border-radius: 7px;
    height: 14px;
    text-align: center;
    color: {text};
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}

QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{
    background: {panel2};
    padding: 7px 10px;
    border-bottom: 1px solid {line};
    font-weight: 600;
}}

QGroupBox {{
    border: 1px solid {line};
    border-radius: 9px;
    margin-top: 14px;
    padding-top: 10px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 11px; padding: 0 5px; color: {dim}; }}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {line};
    border-radius: 4px;
    background: {panel};
}}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}

QSlider::groove:horizontal {{ height: 4px; background: {line}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; margin: -6px 0; border-radius: 7px; background: {accent};
}}

QToolTip {{
    background: {panel2};
    color: {text};
    border: 1px solid {line};
    border-radius: 6px;
    padding: 5px 8px;
}}

QLabel#hint {{ color: {dim}; }}
QLabel#title {{ font-size: 17px; font-weight: 700; }}
"""


def resolve_theme(mode: str) -> str:
    """'auto' resolves against the current system palette."""
    if mode in ("dark", "light"):
        return mode
    app = QtWidgets.QApplication.instance()
    if app is not None:
        window_colour = app.palette().window().color()
        return "dark" if window_colour.lightness() < 128 else "light"
    return "dark"


def apply_theme(app: QtWidgets.QApplication, mode: str) -> str:
    resolved = resolve_theme(mode)
    palette = _PALETTES[resolved]
    app.setStyleSheet(_QSS_TEMPLATE.format(**palette))
    return resolved


def palette_for(mode: str) -> dict[str, str]:
    return _PALETTES[resolve_theme(mode)]


# ---------------------------------------------------------------------------
# frogtool bridge
# ---------------------------------------------------------------------------


def rebuild_system(ctx: JobContext, drive: str, system: str) -> str:
    """Rebuild one system's ROM index via frogtool."""
    frogtool = optional_import("frogtool")
    if frogtool is None:
        raise RuntimeError("frogtool is not available; cannot rebuild ROM lists.")
    ctx.status(f"Rebuilding {system}…")
    return str(frogtool.process_sys(drive, system, False) or "")


def rebuild_all(ctx: JobContext, drive: str) -> dict[str, str]:
    """Rebuild every system, reporting honest per-system progress.

    Madpole's version started the bar at 20 and added 10 per system, so the
    bar was pure decoration and overran past 100 with seven consoles.
    """
    systems = known_systems()
    results: dict[str, str] = {}
    total = len(systems)
    for i, system in enumerate(systems):
        ctx.progress(i, total, f"Rebuilding {system} ({i + 1} of {total})")
        try:
            results[system] = rebuild_system(ctx, drive, system)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad folder must not stop the rest
            log.warning("Rebuild failed for %s: %s", system, exc)
            results[system] = f"error: {exc}"
    ctx.progress(total, total, "Rebuild complete")
    return results


__all__ = [name for name in dir() if not name.startswith("_")]