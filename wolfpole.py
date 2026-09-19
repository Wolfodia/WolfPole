#!/usr/bin/env python3
"""
Wolfpole — SF2000 SD card manager.

Entry point. The interface lives in :mod:`wolfpole_ui`, the machinery in
:mod:`wolfpole_core`; this module only parses the command line, sets up paths
and logging, and either starts the window or runs a headless command.

    python wolfpole.py                              # the app
    python wolfpole.py --portable                   # settings beside the script
    python wolfpole.py --list-drives                # headless
    python wolfpole.py --drive E:\\ --rebuild ALL
    python wolfpole.py --drive E:\\ --backup-saves ~/backups

Drop this file, wolfpole_ui.py and wolfpole_core.py into a Tadpole/Madpole
checkout: Wolfpole reuses frogtool, tadpole_functions, multicore_functions and
the dialogs package when they are importable, and starts with those features
disabled when they are not.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import wolfpole_core as wc
from wolfpole_core import APP_NAME, APP_TAGLINE, APP_VERSION, QtCore, QtWidgets, qexec

log = logging.getLogger(wc.APP_SLUG)


# ---------------------------------------------------------------------------
# Headless commands
# ---------------------------------------------------------------------------


class ConsoleContext(wc.JobContext):
    """JobContext that prints to the terminal instead of driving a widget."""

    def __init__(self) -> None:  # noqa: D107 - deliberately skips super().__init__
        self._cancelled = False
        self._last = ""

    def cancel(self) -> None:
        self._cancelled = True

    def check(self) -> None:
        if self._cancelled:
            raise wc.Cancelled()

    def progress(self, value: int, total: int = 0, text: str = "") -> None:
        if text and text != self._last:
            self._last = text
            print(f"  {text}")

    def status(self, text: str) -> None:
        print(f"  {text}")


def headless(args: argparse.Namespace, config: wc.Config) -> int:
    """Run a command-line action, reporting failures without a traceback."""
    try:
        return _headless(args, config)
    except wc.Cancelled:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI users want one clear line
        log.debug("Headless command failed", exc_info=True)
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return 5


def _headless(args: argparse.Namespace, config: wc.Config) -> int:
    ctx = ConsoleContext()

    if args.list_drives:
        drives = wc.scan_drives(config.local_library())
        if not drives:
            print("No SF2000 cards found.")
            return 1
        for info in drives:
            print(f"{info.mountpoint:<24} {info.describe()}")
        return 0

    drive = args.drive or config.get("last_drive", "")
    if not drive:
        print("No drive given. Use --drive, or --list-drives to see what is connected.",
              file=sys.stderr)
        return 2
    if not Path(drive).exists():
        print(f"{drive} does not exist.", file=sys.stderr)
        return 2

    if args.rebuild:
        target = args.rebuild.upper()
        print(f"Rebuilding {target} on {drive}")
        if target == wc.ALL_SYSTEMS:
            results = wc.rebuild_all(ctx, drive)
            for system, result in results.items():
                print(f"  {system:<8} {result}")
            return 1 if any(str(v).startswith("error:") for v in results.values()) else 0
        print(wc.rebuild_system(ctx, drive, target))
        return 0

    if args.backup_saves:
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "createSaveBackup", None) if tf else None
        if not callable(fn):
            print("tadpole_functions is unavailable, cannot back up.", file=sys.stderr)
            return 3
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = Path(args.backup_saves).expanduser() / f"SF2000SaveBackup_{stamp}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        ok = bool(fn(drive, str(target)))
        print(f"{'Wrote' if ok else 'Failed to write'} {target}")
        return 0 if ok else 4

    print("Nothing to do. Try --rebuild, --backup-saves or --list-drives.", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wolfpole", description=f"{APP_NAME} {APP_VERSION} — {APP_TAGLINE}")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--portable", action="store_true",
                        help="keep settings and logs beside the program")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="debug logging, also to the console")
    parser.add_argument("--theme", choices=("auto", "dark", "light"),
                        help="override the saved theme for this run")
    parser.add_argument("--drive", help="card to open at startup, or to act on headlessly")
    parser.add_argument("--offline", action="store_true",
                        help="disable all network access for this run")
    parser.add_argument("--list-drives", action="store_true",
                        help="print detected SF2000 cards and exit")
    parser.add_argument("--rebuild", metavar="SYSTEM",
                        help="rebuild one system's ROM index (or ALL) and exit")
    parser.add_argument("--backup-saves", metavar="FOLDER",
                        help="write a save backup into FOLDER and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    paths = wc.resolve_paths(portable=args.portable or None)
    wc.setup_logging(paths, verbose=args.verbose)
    config = wc.Config(paths)
    if args.offline:
        config.set("network", 0)
    if args.theme:
        config.set("theme", args.theme)

    if args.list_drives or args.rebuild or args.backup_saves:
        return headless(args, config)

    # Imported here so the headless paths never need a display.
    import wolfpole_ui as ui

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    palette = ui.apply_shell_theme(app, config.get("theme"))

    window = ui.WolfpoleWindow(paths, config, palette, initial_drive=args.drive)
    window.show()

    missing = [name for name in ("frogtool", "tadpole_functions")
               if wc.optional_import(name) is None]
    if missing:
        QtCore.QTimer.singleShot(600, lambda: window.notify(
            "Running with reduced features — " + ", ".join(missing) + " not found",
            "bad",
            ("Why?", lambda: window.tell(
                "Reduced features",
                "Wolfpole could not import: " + ", ".join(missing) + ".\n\n"
                "Browsing, artwork and deletion still work, but rebuilding the ROM "
                "index and the firmware tools are disabled. Run Wolfpole from the "
                "folder holding the Tadpole/Madpole modules to enable everything."))))

    return qexec(app)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - last-resort crash handler
        logging.getLogger(wc.APP_SLUG).exception("Fatal error")
        print(f"{APP_NAME} crashed: {exc}", file=sys.stderr)
        sys.exit(1)