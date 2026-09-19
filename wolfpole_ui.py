"""
wolfpole_ui.py — the Wolfpole interface.

A different shell from the toolbar-and-table layout the Tadpole family has
always used:

  * a persistent sidebar: card picker with a capacity ring, system list with
    live counts, and a tray of running tasks
  * a cover-art grid as the primary view, with a dense list as the alternative
  * an inspector drawer that slides in for the selected ROM
  * a shortcut dock showing the four home-screen slots as real tiles you can
    click a ROM into
  * a Device page of task cards instead of nested menus
  * a command palette (Ctrl+K) that reaches every action by name
  * toasts and an inline task tray instead of modal progress dialogs and a
    QMessageBox after every operation

There is no menu bar. Nothing blocks the window while work happens.

All infrastructure lives in :mod:`wolfpole_core`; this module is presentation
plus the operations wired to it.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import wolfpole_builder as wb
import wolfpole_core as wc
from wolfpole_core import (
    ALIGN_CENTER,
    ALIGN_LEFT,
    ALIGN_RIGHT,
    ALIGN_VCENTER,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    KEEP_ASPECT,
    NO_DRIVE,
    ORIENT_HORIZONTAL,
    QAction,
    QT_BINDING,
    QtCore,
    QtGui,
    QtWidgets,
    ROLE_USER,
    SELECT_EXTENDED,
    SELECT_ROWS,
    SCROLL_PER_PIXEL,
    SMOOTH_TRANSFORM,
    Signal,
    Qt,
    qenum,
    qexec,
)

log = logging.getLogger(f"{wc.APP_SLUG}.ui")

BASE_DIR = wc.app_dir()

DISCORD_URL = "https://discord.gg/retrohandhelds"
BOOTLOADER_DOC_URL = "https://github.com/vonmillhausen/sf2000#bootloader-bug"
COLLECTION_URL = "https://zerter555.github.io/sf2000-collection/"
BOOTLOADER_SHA256 = "eb7a4e9c8aba9f133696d4ea31c1efa50abd85edc1321ce8917becdc98a66927"
BOOTLOADER_URL = (
    "https://github.com/EricGoldsteinNz/SF2000_Resources/blob/"
    "60659cc783263614c20a60f6e2dd689d319c04f6/OS/Firmware.upk?raw=true"
)

SYSTEM_GLYPH = {
    "FC": "N", "SFC": "S", "MD": "M", "GB": "G",
    "GBC": "C", "GBA": "A", "ARCADE": "▣",
}

# Enum lookups used while painting.
ANTIALIAS = qenum(QtGui.QPainter, "RenderHint", "Antialiasing")
SMOOTH_PIXMAP = qenum(QtGui.QPainter, "RenderHint", "SmoothPixmapTransform")
STATE_SELECTED = qenum(QtWidgets.QStyle, "StateFlag", "State_Selected")
STATE_HOVER = qenum(QtWidgets.QStyle, "StateFlag", "State_MouseOver")
ELIDE_RIGHT = qenum(Qt, "TextElideMode", "ElideRight")
NO_PEN = qenum(Qt, "PenStyle", "NoPen")
ICON_MODE = qenum(QtWidgets.QListView, "ViewMode", "IconMode")
RESIZE_ADJUST = qenum(QtWidgets.QListView, "ResizeMode", "Adjust")
MOVEMENT_STATIC = qenum(QtWidgets.QListView, "Movement", "Static")
POINTING_HAND = qenum(Qt, "CursorShape", "PointingHandCursor")
KEY_ESCAPE = qenum(Qt, "Key", "Key_Escape")
KEY_UP = qenum(Qt, "Key", "Key_Up")
KEY_DOWN = qenum(Qt, "Key", "Key_Down")


# ---------------------------------------------------------------------------
# Shell styling
# ---------------------------------------------------------------------------

_SHELL_QSS = """
#sidebar {{
    background: {panel};
    border: none;
    border-right: 1px solid {line};
}}
#wordmark {{ font-size: 19px; font-weight: 800; letter-spacing: 0.5px; }}
#wordmarkDot {{ color: {accent}; font-size: 19px; font-weight: 800; }}
#sectionLabel {{
    color: {dim};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.4px;
}}

#driveChip {{
    background: {panel2};
    border: 1px solid {line};
    border-radius: 11px;
    padding: 9px 11px;
    text-align: left;
}}
#driveChip:hover {{ border-color: {accent}; }}

QPushButton#nav {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 8px 10px;
    text-align: left;
    color: {dim};
    font-weight: 600;
}}
QPushButton#nav:hover {{ background: {panel2}; color: {text}; }}
QPushButton#nav:checked {{ background: {sel}; color: {text}; border-color: {line}; }}

#countBadge {{
    background: {panel2};
    color: {dim};
    border-radius: 8px;
    padding: 1px 7px;
    font-size: 11px;
    font-weight: 700;
}}

#header {{ background: {bg}; border-bottom: 1px solid {line}; }}
#pageTitle {{ font-size: 21px; font-weight: 700; }}
#pageSubtitle {{ color: {dim}; font-size: 12px; }}

#searchField {{
    background: {panel};
    border: 1px solid {line};
    border-radius: 9px;
    padding: 7px 12px;
    min-width: 230px;
}}
#searchField:focus {{ border-color: {accent}; }}

QToolButton#segment {{
    background: {panel};
    border: 1px solid {line};
    border-radius: 8px;
    padding: 6px 12px;
    color: {dim};
    font-weight: 600;
}}
QToolButton#segment:checked {{ background: {sel}; color: {text}; border-color: {accent}; }}

#slotTile {{
    background: {panel};
    border: 1px dashed {line};
    border-radius: 12px;
}}
#slotTile[filled="true"] {{ border-style: solid; border-color: {line}; background: {panel2}; }}
#slotTile:hover {{ border-color: {accent}; }}
#slotIndex {{ color: {dim}; font-size: 10px; font-weight: 800; letter-spacing: 1px; }}
#slotName {{ font-size: 11px; font-weight: 600; }}

#inspector {{ background: {panel}; border-left: 1px solid {line}; }}
#inspectorArt {{ background: {panel2}; border-radius: 12px; }}
#factKey {{ color: {dim}; font-size: 11px; font-weight: 600; }}
#factValue {{ font-size: 12px; }}

#toolCard {{
    background: {panel};
    border: 1px solid {line};
    border-radius: 13px;
}}
#toolCard:hover {{ border-color: {accent}; }}
#toolTitle {{ font-size: 14px; font-weight: 700; }}
#toolBody {{ color: {dim}; font-size: 12px; }}
#toolCard[tone="danger"] #toolTitle {{ color: {danger}; }}

#taskRow {{ background: {panel2}; border: 1px solid {line}; border-radius: 9px; }}
#taskTitle {{ font-size: 11px; font-weight: 600; }}
#taskDetail {{ color: {dim}; font-size: 10px; }}
#miniBar {{ max-height: 4px; min-height: 4px; border: none; background: {line}; border-radius: 2px; }}
#miniBar::chunk {{ background: {accent}; border-radius: 2px; }}
QPushButton#taskStop {{
    background: transparent; border: none; color: {dim};
    padding: 0 4px; font-size: 14px; font-weight: 700;
}}
QPushButton#taskStop:hover {{ color: {danger}; }}

#toast {{
    background: {panel2};
    border: 1px solid {line};
    border-left: 3px solid {accent};
    border-radius: 10px;
}}
#toast[tone="bad"] {{ border-left-color: {danger}; }}
#toast[tone="good"] {{ border-left-color: {ok}; }}
#toastText {{ font-size: 12px; }}

#sheet {{ background: {panel}; border: 1px solid {line}; border-radius: 14px; }}
#sheetTitle {{ font-size: 16px; font-weight: 700; }}
#sheetBody {{ color: {dim}; font-size: 12px; }}

#paletteInput {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {line};
    border-radius: 0;
    padding: 12px 6px;
    font-size: 16px;
}}
#paletteList {{ background: transparent; border: none; }}
#paletteList::item {{ padding: 8px 10px; border-radius: 8px; color: {text}; }}
#paletteList::item:selected {{ background: {sel}; }}

#emptyTitle {{ font-size: 17px; font-weight: 700; }}
#emptyBody {{ color: {dim}; font-size: 13px; }}
"""


def shell_stylesheet(palette: dict[str, str]) -> str:
    return _SHELL_QSS.format(**palette)


def apply_shell_theme(app: QtWidgets.QApplication, mode: str) -> dict[str, str]:
    """Core theme plus the shell rules, returning the resolved palette."""
    wc.apply_theme(app, mode)
    palette = wc.palette_for(mode)
    app.setStyleSheet(app.styleSheet() + shell_stylesheet(palette))
    return palette


def restyle(widget: QtWidgets.QWidget) -> None:
    """Re-evaluate a widget's stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def label(text: str, name: str = "", wrap: bool = False) -> QtWidgets.QLabel:
    widget = QtWidgets.QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


# ---------------------------------------------------------------------------
# Toasts
# ---------------------------------------------------------------------------


class Toast(QtWidgets.QFrame):
    """A small message that appears bottom-right and fades out.

    Replaces the QMessageBox that the original fired after every single
    operation, each of which stole focus and needed dismissing.
    """

    closed = Signal(object)

    def __init__(self, text: str, tone: str, parent: QtWidgets.QWidget,
                 action: tuple[str, Callable[[], None]] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setProperty("tone", tone)
        self.setFixedWidth(360)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 12, 11)
        layout.setSpacing(10)

        message = label(text, "toastText", wrap=True)
        layout.addWidget(message, 1)

        if action is not None:
            button = QtWidgets.QPushButton(action[0])
            button.setCursor(POINTING_HAND)
            button.clicked.connect(lambda: (action[1](), self.dismiss()))
            layout.addWidget(button)

        close = QtWidgets.QPushButton("×")
        close.setObjectName("taskStop")
        close.setFixedWidth(22)
        close.setCursor(POINTING_HAND)
        close.clicked.connect(self.dismiss)
        layout.addWidget(close)

        self._effect = QtWidgets.QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

        self._fade = QtCore.QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(160)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(6000 if action is None else 9000)

    def dismiss(self) -> None:
        self._timer.stop()
        out = QtCore.QPropertyAnimation(self._effect, b"opacity", self)
        out.setDuration(140)
        out.setStartValue(self._effect.opacity())
        out.setEndValue(0.0)
        out.finished.connect(lambda: self.closed.emit(self))
        out.start()
        self._out = out  # keep a reference alive


class ToastHost(QtCore.QObject):
    """Stacks toasts above the bottom-right corner of the window."""

    def __init__(self, window: QtWidgets.QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._toasts: list[Toast] = []

    def show(self, text: str, tone: str = "info",
             action: tuple[str, Callable[[], None]] | None = None) -> None:
        toast = Toast(text, tone, self._window, action)
        toast.closed.connect(self._remove)
        self._toasts.append(toast)
        if len(self._toasts) > 4:
            self._toasts[0].dismiss()
        toast.show()
        self.reposition()

    def _remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        toast.deleteLater()
        self.reposition()

    def reposition(self) -> None:
        margin = 18
        y = self._window.height() - margin
        for toast in reversed(self._toasts):
            toast.adjustSize()
            y -= toast.height()
            toast.move(self._window.width() - toast.width() - margin, y)
            y -= 8
            toast.raise_()


# ---------------------------------------------------------------------------
# Task tray
# ---------------------------------------------------------------------------


class ProgressProxy(QtCore.QObject):
    """Thread-safe stand-in for a QProgressBar.

    Legacy Tadpole helpers write progress straight into a widget. They now run
    in worker threads, so every call hops back to the GUI thread through a
    queued signal before anything is painted.
    """

    valueChanged = Signal(int)
    maximumChanged = Signal(int)
    resetRequested = Signal()

    def setValue(self, value: int) -> None:
        self.valueChanged.emit(int(value))

    def setMaximum(self, value: int) -> None:
        self.maximumChanged.emit(int(value))

    def setMinimum(self, value: int) -> None:
        return None

    def setRange(self, low: int, high: int) -> None:
        self.maximumChanged.emit(int(high))

    def reset(self) -> None:
        self.resetRequested.emit()

    def value(self) -> int:
        return 0


class TaskRow(QtWidgets.QFrame):
    """One running job in the sidebar tray."""

    stopRequested = Signal()

    def __init__(self, title: str, cancellable: bool,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskRow")

        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(10, 8, 8, 9)
        layout.setSpacing(4)

        self.title = label(title, "taskTitle")
        layout.addWidget(self.title, 0, 0)

        self.stop = QtWidgets.QPushButton("×")
        self.stop.setObjectName("taskStop")
        self.stop.setFixedWidth(18)
        self.stop.setCursor(POINTING_HAND)
        self.stop.setVisible(cancellable)
        self.stop.clicked.connect(self.stopRequested)
        layout.addWidget(self.stop, 0, 1)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("miniBar")
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar, 1, 0, 1, 2)

        self.detail = label("working…", "taskDetail")
        layout.addWidget(self.detail, 2, 0, 1, 2)


class TaskHandle(QtCore.QObject):
    """Controls one tray row, and duck-types Tadpole's progress dialog.

    Passing this to ``downloadAndExtractZIPBar`` or ``BatteryPatcher`` works
    exactly as the old modal dialog did, except the window stays usable.
    """

    textRequested = Signal(str)
    detailRequested = Signal(str)

    def __init__(self, row: TaskRow, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.row = row
        self.job: wc.Job | None = None
        self.progress = ProgressProxy(self)
        self.progress.valueChanged.connect(self._value)
        self.progress.maximumChanged.connect(self._maximum)
        self.progress.resetRequested.connect(self.row.bar.reset)
        self.textRequested.connect(self.row.title.setText)
        self.detailRequested.connect(self.row.detail.setText)
        self.row.stopRequested.connect(self.cancel)

    # -- legacy API, safe from any thread ---------------------------------
    def setText(self, text: str) -> None:
        self.textRequested.emit(str(text))

    def showProgress(self, value: int, _visible: bool = True) -> None:
        self.progress.setValue(int(value))

    def close(self) -> None:  # legacy helpers call this when finished
        return None

    def show(self) -> None:
        return None

    # -- internals ---------------------------------------------------------
    def _value(self, value: int) -> None:
        if self.row.bar.maximum() == 0:
            self.row.bar.setRange(0, 100)
        self.row.bar.setValue(value)

    def _maximum(self, value: int) -> None:
        self.row.bar.setRange(0, max(1, value))

    def bind(self, job: wc.Job) -> None:
        self.job = job

    def cancel(self) -> None:
        self.row.detail.setText("cancelling…")
        if self.job is not None:
            self.job.cancel()

    def on_progress(self, value: int, total: int, text: str) -> None:
        if total > 0:
            self.row.bar.setRange(0, total)
            self.row.bar.setValue(value)
        if text:
            self.row.detail.setText(text)

    def on_message(self, text: str) -> None:
        self.row.detail.setText(text)


class TaskTray(QtWidgets.QWidget):
    """Live list of background work, pinned to the bottom of the sidebar."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._rows = 0
        self.setVisible(False)

    def start(self, title: str, cancellable: bool = True) -> TaskHandle:
        row = TaskRow(title, cancellable, self)
        self._layout.addWidget(row)
        self._rows += 1
        self.setVisible(True)
        return TaskHandle(row, self)

    def finish(self, handle: TaskHandle) -> None:
        handle.row.setParent(None)
        handle.row.deleteLater()
        self._rows = max(0, self._rows - 1)
        self.setVisible(self._rows > 0)

    @property
    def busy(self) -> bool:
        return self._rows > 0


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


class CapacityRing(QtWidgets.QWidget):
    """Donut showing how full the card is."""

    def __init__(self, palette: dict[str, str], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._fraction = 0.0
        self.setFixedSize(34, 34)

    def set_palette_colours(self, palette: dict[str, str]) -> None:
        self._palette = palette
        self.update()

    def set_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(ANTIALIAS, True)
        rect = QtCore.QRectF(3, 3, self.width() - 6, self.height() - 6)

        track = QtGui.QPen(QtGui.QColor(self._palette["line"]), 4)
        painter.setPen(track)
        painter.drawEllipse(rect)

        colour = self._palette["accent"] if self._fraction < 0.9 else self._palette["danger"]
        arc = QtGui.QPen(QtGui.QColor(colour), 4)
        arc.setCapStyle(qenum(Qt, "PenCapStyle", "RoundCap"))
        painter.setPen(arc)
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * self._fraction))
        painter.end()


class DriveChip(QtWidgets.QPushButton):
    """Card picker: name, free space and a capacity ring in one control."""

    def __init__(self, palette: dict[str, str], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("driveChip")
        self.setCursor(POINTING_HAND)
        self.setMinimumHeight(58)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(10)

        self.ring = CapacityRing(palette, self)
        layout.addWidget(self.ring)

        text = QtWidgets.QVBoxLayout()
        text.setSpacing(1)
        self.primary = label("No card", "slotName")
        self.secondary = label("insert an SF2000 card", "taskDetail")
        text.addWidget(self.primary)
        text.addWidget(self.secondary)
        layout.addLayout(text, 1)

        self.chevron = label("⌄", "sectionLabel")
        layout.addWidget(self.chevron)

    def set_drive(self, info: wc.DriveInfo | None) -> None:
        if info is None:
            self.primary.setText("No card")
            self.secondary.setText("insert an SF2000 card")
            self.ring.set_fraction(0.0)
            return
        name = "Local library" if info.local else Path(info.mountpoint).name or info.mountpoint
        self.primary.setText(name)
        if info.total:
            self.secondary.setText(f"{wc.human_size(info.free)} free")
            self.ring.set_fraction((info.total - info.free) / info.total)
        else:
            self.secondary.setText(info.mountpoint)
            self.ring.set_fraction(0.0)


class NavButton(QtWidgets.QPushButton):
    """Sidebar entry with an optional count badge."""

    def __init__(self, text: str, glyph: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(POINTING_HAND)
        self.setMinimumHeight(34)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(9)

        if glyph:
            mark = label(glyph, "sectionLabel")
            mark.setFixedWidth(14)
            mark.setAlignment(ALIGN_CENTER)
            layout.addWidget(mark)

        self.caption = QtWidgets.QLabel(text)
        layout.addWidget(self.caption, 1)

        self.badge = label("", "countBadge")
        self.badge.setVisible(False)
        layout.addWidget(self.badge)

    def set_count(self, count: int | None) -> None:
        if count is None:
            self.badge.setVisible(False)
            return
        self.badge.setText(str(count))
        self.badge.setVisible(True)


class Sidebar(QtWidgets.QFrame):
    driveClicked = Signal()
    systemSelected = Signal(str)
    pageSelected = Signal(str)
    activityToggled = Signal(bool)

    def __init__(self, palette: dict[str, str], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(232)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(12)

        wordmark = QtWidgets.QHBoxLayout()
        wordmark.setSpacing(2)
        wordmark.addWidget(label(APP_NAME, "wordmark"))
        wordmark.addWidget(label("•", "wordmarkDot"))
        wordmark.addStretch(1)
        layout.addLayout(wordmark)

        self.chip = DriveChip(palette, self)
        self.chip.clicked.connect(self.driveClicked)
        layout.addWidget(self.chip)

        layout.addWidget(label("SYSTEMS", "sectionLabel"))
        self.system_buttons: dict[str, NavButton] = {}
        for system in wc.known_systems():
            button = NavButton(wc.system_label(system), SYSTEM_GLYPH.get(system, "•"), self)
            button.clicked.connect(lambda _=False, s=system: self._pick_system(s))
            layout.addWidget(button)
            self.system_buttons[system] = button

        layout.addSpacing(6)
        layout.addWidget(label("CARD", "sectionLabel"))
        self.page_buttons: dict[str, NavButton] = {}
        for key, text, glyph in (("overview", "Overview", "◉"),
                                 ("device", "Device & firmware", "⚙"),
                                 ("multicore", "Multicore", "⧉"),
                                 ("build", "Build a card", "⬒"),
                                 ("rescue", "Rescue", "✚")):
            button = NavButton(text, glyph, self)
            button.clicked.connect(lambda _=False, k=key: self._pick_page(k))
            layout.addWidget(button)
            self.page_buttons[key] = button

        layout.addStretch(1)

        self.tray = TaskTray(self)
        layout.addWidget(self.tray)

        self.activity_button = QtWidgets.QPushButton("Activity log")
        self.activity_button.setObjectName("nav")
        self.activity_button.setCheckable(True)
        self.activity_button.setCursor(POINTING_HAND)
        self.activity_button.toggled.connect(self.activityToggled)
        layout.addWidget(self.activity_button)

        hint = label("⌘K / Ctrl+K  —  commands", "taskDetail")
        hint.setAlignment(ALIGN_CENTER)
        layout.addWidget(hint)

    def _pick_system(self, system: str) -> None:
        self.select_system(system)
        self.systemSelected.emit(system)

    def _pick_page(self, key: str) -> None:
        self.select_page(key)
        self.pageSelected.emit(key)

    def select_system(self, system: str) -> None:
        for name, button in self.system_buttons.items():
            button.setChecked(name == system)
        for button in self.page_buttons.values():
            button.setChecked(False)

    def select_page(self, key: str) -> None:
        for button in self.system_buttons.values():
            button.setChecked(False)
        for name, button in self.page_buttons.items():
            button.setChecked(name == key)

    def set_counts(self, counts: dict[str, int]) -> None:
        for system, button in self.system_buttons.items():
            button.set_count(counts.get(system))

    def set_enabled_for_card(self, enabled: bool) -> None:
        for button in self.system_buttons.values():
            button.setEnabled(enabled)
        for key, button in self.page_buttons.items():
            # Building a card is the one thing you do *without* a working card.
            button.setEnabled(True if key == "build" else enabled)


# ---------------------------------------------------------------------------
# Cover grid
# ---------------------------------------------------------------------------


class CoverDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a ROM as a cover tile: art, title, size and slot badge."""

    def __init__(self, cache: wc.ThumbnailCache, palette: dict[str, str],
                 parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.palette = palette
        self.scale = 1.0
        self.show_system = False

    def tile_size(self) -> QtCore.QSize:
        width = int(wc.THUMB_W * self.scale)
        height = int(wc.THUMB_H * self.scale) + 46
        return QtCore.QSize(width + 18, height + 14)

    def sizeHint(self, option, index) -> QtCore.QSize:
        return self.tile_size()

    def paint(self, painter: QtGui.QPainter, option, index) -> None:
        rom = index.data(ROLE_USER)
        if not isinstance(rom, wc.RomEntry):
            return
        painter.save()
        painter.setRenderHint(ANTIALIAS, True)
        painter.setRenderHint(SMOOTH_PIXMAP, True)

        body = QtCore.QRectF(option.rect).adjusted(5, 5, -5, -5)
        selected = bool(option.state & STATE_SELECTED)
        hovered = bool(option.state & STATE_HOVER)

        if selected or hovered:
            painter.setPen(NO_PEN)
            painter.setBrush(QtGui.QColor(self.palette["sel" if selected else "panel2"]))
            painter.drawRoundedRect(body, 12, 12)

        art_height = int(wc.THUMB_H * self.scale)
        art_width = int(wc.THUMB_W * self.scale)
        art = QtCore.QRectF(
            body.left() + (body.width() - art_width) / 2, body.top() + 6,
            art_width, art_height,
        )

        pixmap = self.cache.pixmap(rom.path) if rom.has_thumbnail else None
        painter.setPen(NO_PEN)
        painter.setBrush(QtGui.QColor(self.palette["panel2"]))
        painter.drawRoundedRect(art, 9, 9)

        if pixmap is not None and not pixmap.isNull():
            path = QtGui.QPainterPath()
            path.addRoundedRect(art, 9, 9)
            painter.setClipPath(path)
            scaled = pixmap.scaled(
                art.size().toSize(),
                qenum(Qt, "AspectRatioMode", "KeepAspectRatioByExpanding"),
                SMOOTH_TRANSFORM,
            )
            painter.drawPixmap(art.topLeft(), scaled)
            painter.setClipping(False)
        else:
            painter.setPen(QtGui.QColor(self.palette["dim"]))
            font = painter.font()
            font.setPointSize(max(10, int(13 * self.scale)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(art, int(ALIGN_CENTER), rom.name[:2].upper())

        if selected:
            painter.setBrush(QtGui.QColor(0, 0, 0, 0))
            painter.setPen(QtGui.QPen(QtGui.QColor(self.palette["accent"]), 2))
            painter.drawRoundedRect(art.adjusted(-1, -1, 1, 1), 10, 10)

        if rom.slot:
            badge = QtCore.QRectF(art.right() - 24, art.top() + 6, 18, 18)
            painter.setPen(NO_PEN)
            painter.setBrush(QtGui.QColor(self.palette["accent"]))
            painter.drawEllipse(badge)
            painter.setPen(QtGui.QColor("#1a1205"))
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(badge, int(ALIGN_CENTER), str(rom.slot))

        text_font = painter.font()
        text_font.setBold(False)
        text_font.setPointSize(9)
        painter.setFont(text_font)
        metrics = QtGui.QFontMetrics(text_font)

        title_rect = QtCore.QRect(
            int(body.left()) + 4, int(art.bottom()) + 7, int(body.width()) - 8, 16
        )
        painter.setPen(QtGui.QColor(self.palette["text"]))
        painter.drawText(
            title_rect, int(ALIGN_LEFT | ALIGN_VCENTER),
            metrics.elidedText(rom.name, ELIDE_RIGHT, title_rect.width()),
        )

        meta_rect = QtCore.QRect(title_rect.left(), title_rect.bottom(), title_rect.width(), 14)
        painter.setPen(QtGui.QColor(self.palette["dim"]))
        meta = wc.human_size(rom.size)
        if self.show_system:
            meta = f"{rom.system}  ·  {meta}"
        if rom.core:
            meta += f"  ·  {rom.core}"
        painter.drawText(
            meta_rect, int(ALIGN_LEFT | ALIGN_VCENTER),
            metrics.elidedText(meta, ELIDE_RIGHT, meta_rect.width()),
        )
        painter.restore()


class CoverGrid(QtWidgets.QListView):
    """Icon view that accepts an image dropped onto a specific cover.

    Dropping a PNG on a tile is the fastest way to fix one wrong piece of box
    art, and it is the interaction people expect from a grid of pictures.
    """

    artworkDropped = Signal(object, str)
    romsDropped = Signal(list)

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)

    @staticmethod
    def _split(urls: list[QtCore.QUrl]) -> tuple[list[str], list[str]]:
        images, roms = [], []
        for url in urls:
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            suffix = Path(path).suffix.lower()
            if suffix in CoverGrid.IMAGE_SUFFIXES:
                images.append(path)
            elif suffix in wc.ROM_EXTENSIONS:
                roms.append(path)
        return images, roms

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            images, roms = self._split(event.mimeData().urls())
            if images or roms:
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        images, roms = self._split(event.mimeData().urls())
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        index = self.indexAt(position)
        rom = index.data(ROLE_USER) if index.isValid() else None
        if images and isinstance(rom, wc.RomEntry):
            event.acceptProposedAction()
            self.artworkDropped.emit(rom, images[0])
            return
        if roms:
            event.acceptProposedAction()
            self.romsDropped.emit(roms)
            return
        event.ignore()


# ---------------------------------------------------------------------------
# Shortcut dock
# ---------------------------------------------------------------------------


class SlotTile(QtWidgets.QFrame):
    """One of the four home-screen shortcut slots."""

    clicked = Signal(int)
    cleared = Signal(int)

    def __init__(self, slot: int, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.slot = slot
        self.setObjectName("slotTile")
        self.setProperty("filled", "false")
        self.setFixedSize(150, 54)
        self.setCursor(POINTING_HAND)
        self.setAcceptDrops(True)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(9)

        self.art = QtWidgets.QLabel()
        self.art.setFixedSize(26, 38)
        self.art.setAlignment(ALIGN_CENTER)
        layout.addWidget(self.art)

        text = QtWidgets.QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(f"SLOT {slot}", "slotIndex"))
        self.caption = label("empty", "slotName")
        self.caption.setWordWrap(False)
        text.addWidget(self.caption)
        layout.addLayout(text, 1)

        self.clear_button = QtWidgets.QPushButton("×")
        self.clear_button.setObjectName("taskStop")
        self.clear_button.setFixedWidth(18)
        self.clear_button.setVisible(False)
        self.clear_button.clicked.connect(lambda: self.cleared.emit(self.slot))
        layout.addWidget(self.clear_button)

    def set_rom(self, rom: wc.RomEntry | None, pixmap: QtGui.QPixmap | None) -> None:
        filled = rom is not None
        self.setProperty("filled", "true" if filled else "false")
        restyle(self)
        self.clear_button.setVisible(filled)
        if rom is None:
            self.caption.setText("empty")
            self.art.clear()
            self.setToolTip("Select a ROM, then click here to put it in this slot")
            return
        metrics = QtGui.QFontMetrics(self.caption.font())
        self.caption.setText(metrics.elidedText(rom.name, ELIDE_RIGHT, 88))
        self.setToolTip(rom.name)
        if pixmap is not None and not pixmap.isNull():
            self.art.setPixmap(pixmap.scaled(
                self.art.size(), KEEP_ASPECT, SMOOTH_TRANSFORM))
        else:
            self.art.clear()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.clicked.emit(self.slot)
        super().mousePressEvent(event)


class ShortcutDock(QtWidgets.QFrame):
    """The four slots, shown above the library."""

    assignRequested = Signal(int)
    clearRequested = Signal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        caption = label("HOME SHORTCUTS", "sectionLabel")
        caption.setFixedWidth(84)
        caption.setWordWrap(True)
        layout.addWidget(caption)

        self.tiles: dict[int, SlotTile] = {}
        for slot in (1, 2, 3, 4):
            tile = SlotTile(slot, self)
            tile.clicked.connect(self.assignRequested)
            tile.cleared.connect(self.clearRequested)
            layout.addWidget(tile)
            self.tiles[slot] = tile
        layout.addStretch(1)


# ---------------------------------------------------------------------------
# Library page
# ---------------------------------------------------------------------------


class EmptyState(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(ALIGN_CENTER)
        layout.setSpacing(8)
        self.title = label("", "emptyTitle")
        self.title.setAlignment(ALIGN_CENTER)
        self.body = label("", "emptyBody", wrap=True)
        self.body.setAlignment(ALIGN_CENTER)
        self.body.setMaximumWidth(420)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        self.action = QtWidgets.QPushButton()
        self.action.setObjectName("primary")
        self.action.setVisible(False)
        layout.addWidget(self.action, 0, ALIGN_CENTER)

    def set(self, title: str, body: str, action: tuple[str, Callable[[], None]] | None = None) -> None:
        self.title.setText(title)
        self.body.setText(body)
        try:
            self.action.clicked.disconnect()
        except TypeError:
            pass
        if action is None:
            self.action.setVisible(False)
        else:
            self.action.setText(action[0])
            self.action.clicked.connect(lambda: action[1]())
            self.action.setVisible(True)


class LibraryPage(QtWidgets.QWidget):
    """Cover grid or dense list, with its own header."""

    searchChanged = Signal(str)
    viewChanged = Signal(str)
    sortRequested = Signal(int)
    zoomChanged = Signal(int)
    selectionChanged = Signal()
    activated = Signal(object)
    contextRequested = Signal(object)
    addRequested = Signal()
    artworkRequested = Signal()
    rebuildRequested = Signal()
    filterChanged = Signal(str)
    scopeChanged = Signal(bool)
    toolsRequested = Signal()

    def __init__(self, model: wc.RomModel, proxy: wc.RomFilterProxy,
                 cache: wc.ThumbnailCache, palette: dict[str, str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.proxy = proxy

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header ----------------------------------------------------------
        header = QtWidgets.QFrame()
        header.setObjectName("header")
        head = QtWidgets.QVBoxLayout(header)
        head.setContentsMargins(22, 16, 22, 14)
        head.setSpacing(12)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(12)
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(1)
        self.title = label("Library", "pageTitle")
        self.subtitle = label("", "pageSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        top.addLayout(titles, 1)

        self.search = QtWidgets.QLineEdit()
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Search this system…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.searchChanged)
        top.addWidget(self.search)

        self.add_button = QtWidgets.QPushButton("Add ROMs")
        self.add_button.setObjectName("primary")
        self.add_button.setCursor(POINTING_HAND)
        self.add_button.clicked.connect(self.addRequested)
        top.addWidget(self.add_button)

        self.art_button = QtWidgets.QPushButton("Artwork")
        self.art_button.setCursor(POINTING_HAND)
        self.art_button.clicked.connect(self.artworkRequested)
        top.addWidget(self.art_button)

        self.rebuild_button = QtWidgets.QPushButton("Rebuild")
        self.rebuild_button.setCursor(POINTING_HAND)
        self.rebuild_button.setToolTip("Rebuild this system's ROM index (F5)")
        self.rebuild_button.clicked.connect(self.rebuildRequested)
        top.addWidget(self.rebuild_button)

        self.grid_button = QtWidgets.QToolButton()
        self.grid_button.setObjectName("segment")
        self.grid_button.setText("Grid")
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(True)
        self.grid_button.clicked.connect(lambda: self.viewChanged.emit("grid"))
        top.addWidget(self.grid_button)

        self.list_button = QtWidgets.QToolButton()
        self.list_button.setObjectName("segment")
        self.list_button.setText("List")
        self.list_button.setCheckable(True)
        self.list_button.clicked.connect(lambda: self.viewChanged.emit("list"))
        top.addWidget(self.list_button)

        self.sort_button = QtWidgets.QToolButton()
        self.sort_button.setObjectName("segment")
        self.sort_button.setText("Sort: Name")
        self.sort_button.setPopupMode(
            qenum(QtWidgets.QToolButton, "ToolButtonPopupMode", "InstantPopup"))
        sort_menu = QtWidgets.QMenu(self.sort_button)
        for text, column in (("Name", wc.RomModel.COL_NAME),
                             ("Size", wc.RomModel.COL_SIZE),
                             ("Format", wc.RomModel.COL_FORMAT),
                             ("Core", wc.RomModel.COL_CORE),
                             ("Shortcut", wc.RomModel.COL_SLOT)):
            action = QAction(text, self)
            action.triggered.connect(
                lambda _=False, c=column, t=text: self._sort(c, t))
            sort_menu.addAction(action)
        self.sort_button.setMenu(sort_menu)
        top.addWidget(self.sort_button)

        self.zoom = QtWidgets.QSlider(ORIENT_HORIZONTAL)
        self.zoom.setRange(60, 160)
        self.zoom.setFixedWidth(84)
        self.zoom.setToolTip("Cover size")
        self.zoom.valueChanged.connect(self.zoomChanged)
        top.addWidget(self.zoom)

        head.addLayout(top)

        self.dock = ShortcutDock(self)
        head.addWidget(self.dock)

        chips = QtWidgets.QHBoxLayout()
        chips.setSpacing(6)
        self.filters: dict[str, QtWidgets.QToolButton] = {}
        for key, text in (("all", "Everything"), ("no_art", "No artwork"),
                          ("shortcuts", "Shortcuts"), ("multicore", "Multicore"),
                          ("large", "Large files")):
            chip = QtWidgets.QToolButton()
            chip.setObjectName("segment")
            chip.setText(text)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.setCursor(POINTING_HAND)
            chip.clicked.connect(lambda _=False, k=key: self._pick_filter(k))
            chips.addWidget(chip)
            self.filters[key] = chip

        chips.addSpacing(10)
        self.scope_button = QtWidgets.QToolButton()
        self.scope_button.setObjectName("segment")
        self.scope_button.setText("All systems")
        self.scope_button.setCheckable(True)
        self.scope_button.setCursor(POINTING_HAND)
        self.scope_button.setToolTip(
            "Search and browse every console at once (Ctrl+Shift+F)")
        self.scope_button.toggled.connect(self.scopeChanged)
        chips.addWidget(self.scope_button)

        chips.addStretch(1)
        self.tools_button = QtWidgets.QToolButton()
        self.tools_button.setObjectName("segment")
        self.tools_button.setText("Bulk tools  ▾")
        self.tools_button.setCursor(POINTING_HAND)
        self.tools_button.clicked.connect(self.toolsRequested)
        chips.addWidget(self.tools_button)
        head.addLayout(chips)

        outer.addWidget(header)

        # Views -------------------------------------------------------------
        self.stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.grid = CoverGrid()
        self.grid.setModel(proxy)
        self.grid.setModelColumn(wc.RomModel.COL_NAME)
        self.grid.setViewMode(ICON_MODE)
        self.grid.setResizeMode(RESIZE_ADJUST)
        self.grid.setMovement(MOVEMENT_STATIC)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(SELECT_EXTENDED)
        self.grid.setVerticalScrollMode(SCROLL_PER_PIXEL)
        self.grid.setSpacing(6)
        self.grid.setFrameShape(qenum(QtWidgets.QFrame, "Shape", "NoFrame"))
        self.grid.setContextMenuPolicy(qenum(Qt, "ContextMenuPolicy", "CustomContextMenu"))
        self.delegate = CoverDelegate(cache, palette, self.grid)
        self.grid.setItemDelegate(self.delegate)
        self.stack.addWidget(self.grid)

        self.table = QtWidgets.QTableView()
        self.table.setModel(proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(SELECT_ROWS)
        self.table.setSelectionMode(SELECT_EXTENDED)
        self.table.setVerticalScrollMode(SCROLL_PER_PIXEL)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(wc.RomModel.COL_THUMB, True)
        self.table.setItemDelegateForColumn(wc.RomModel.COL_SLOT, wc.SlotDelegate(self.table))
        self.table.setContextMenuPolicy(qenum(Qt, "ContextMenuPolicy", "CustomContextMenu"))
        self.table.setFrameShape(qenum(QtWidgets.QFrame, "Shape", "NoFrame"))
        head_view = self.table.horizontalHeader()
        head_view.setSectionResizeMode(wc.RomModel.COL_NAME, wc.RESIZE_STRETCH)
        for column in (wc.RomModel.COL_SIZE, wc.RomModel.COL_FORMAT,
                       wc.RomModel.COL_CORE, wc.RomModel.COL_SLOT):
            head_view.setSectionResizeMode(column, wc.RESIZE_TO_CONTENTS)
        head_view.setHighlightSections(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.stack.addWidget(self.table)

        self.empty = EmptyState()
        self.stack.addWidget(self.empty)

        for view in (self.grid, self.table):
            view.selectionModel().selectionChanged.connect(self.selectionChanged)
            view.doubleClicked.connect(
                lambda index: self.activated.emit(index.data(ROLE_USER)))
            view.customContextMenuRequested.connect(
                lambda point, v=view: self.contextRequested.emit(
                    v.viewport().mapToGlobal(point)))

        self._view = "grid"

    def _pick_filter(self, key: str) -> None:
        for name, chip in self.filters.items():
            chip.setChecked(name == key)
        self.filterChanged.emit(key)

    def set_filter(self, key: str) -> None:
        for name, chip in self.filters.items():
            chip.setChecked(name == key)

    def _sort(self, column: int, text: str) -> None:
        self.sort_button.setText(f"Sort: {text}")
        self.sortRequested.emit(column)

    def current_view(self) -> QtWidgets.QAbstractItemView:
        return self.grid if self._view == "grid" else self.table

    def set_view(self, kind: str) -> None:
        self._view = kind
        self.grid_button.setChecked(kind == "grid")
        self.list_button.setChecked(kind == "list")
        self.zoom.setVisible(kind == "grid")
        self.show_content(True)

    def show_content(self, has_rows: bool) -> None:
        if not has_rows:
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(self.grid if self._view == "grid" else self.table)

    def set_zoom(self, value: int) -> None:
        self.delegate.scale = value / 100.0
        self.grid.setGridSize(self.delegate.tile_size())
        self.grid.reset()

    def selected_roms(self) -> list[wc.RomEntry]:
        view = self.current_view()
        indexes = view.selectionModel().selectedRows() if view is self.table \
            else view.selectionModel().selectedIndexes()
        roms = []
        for index in indexes:
            rom = index.data(ROLE_USER)
            if isinstance(rom, wc.RomEntry) and rom not in roms:
                roms.append(rom)
        return roms


# ---------------------------------------------------------------------------
# Inspector drawer
# ---------------------------------------------------------------------------


class Inspector(QtWidgets.QFrame):
    """Slide-in detail panel for the current selection."""

    WIDTH = 312

    replaceArt = Signal(object)
    exportArt = Signal(object)
    reveal = Signal(object)
    rename = Signal(object)
    remove = Signal(object)
    assignSlot = Signal(object, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspector")
        self.setMaximumWidth(0)
        self._rom: wc.RomEntry | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.art = QtWidgets.QLabel()
        self.art.setObjectName("inspectorArt")
        self.art.setMinimumHeight(250)
        self.art.setAlignment(ALIGN_CENTER)
        layout.addWidget(self.art)

        self.title = label("", "sheetTitle", wrap=True)
        layout.addWidget(self.title)

        self.facts = QtWidgets.QFormLayout()
        self.facts.setSpacing(5)
        self.facts.setLabelAlignment(ALIGN_RIGHT | ALIGN_VCENTER)
        self._fields: dict[str, QtWidgets.QLabel] = {}
        for key in ("File", "Size", "Format", "Core", "Modified"):
            value = label("—", "factValue", wrap=True)
            self._fields[key] = value
            self.facts.addRow(label(key, "factKey"), value)
        layout.addLayout(self.facts)

        layout.addWidget(label("SHORTCUT SLOT", "sectionLabel"))
        chips = QtWidgets.QHBoxLayout()
        chips.setSpacing(6)
        self.slot_buttons: dict[int, QtWidgets.QPushButton] = {}
        for slot in (0, 1, 2, 3, 4):
            button = QtWidgets.QPushButton("None" if slot == 0 else str(slot))
            button.setCheckable(True)
            button.setCursor(POINTING_HAND)
            button.clicked.connect(
                lambda _=False, s=slot: self._rom and self.assignSlot.emit(self._rom, s))
            chips.addWidget(button)
            self.slot_buttons[slot] = button
        layout.addLayout(chips)

        layout.addStretch(1)

        for text, signal, style in (
            ("Replace artwork…", self.replaceArt, ""),
            ("Export artwork…", self.exportArt, ""),
            ("Rename…", self.rename, ""),
            ("Show in file manager", self.reveal, ""),
            ("Delete", self.remove, "danger"),
        ):
            button = QtWidgets.QPushButton(text)
            if style:
                button.setObjectName(style)
            button.clicked.connect(
                lambda _=False, s=signal: self._rom and s.emit(self._rom))
            layout.addWidget(button)
            if text.startswith("Export"):
                self.export_button = button

        self._animation = QtCore.QPropertyAnimation(self, b"maximumWidth", self)
        self._animation.setDuration(170)

    def set_open(self, open_: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self.maximumWidth())
        self._animation.setEndValue(self.WIDTH if open_ else 0)
        self._animation.start()

    def show_rom(self, rom: wc.RomEntry | None, pixmap: QtGui.QPixmap | None) -> None:
        self._rom = rom
        if rom is None:
            self.set_open(False)
            return
        self.set_open(True)
        self.title.setText(rom.name)
        self._fields["File"].setText(rom.filename)
        self._fields["Size"].setText(wc.human_size(rom.size))
        self._fields["Format"].setText(rom.ext.lstrip(".").upper() or "—")
        self._fields["Core"].setText(rom.core or ("stock" if rom.ext != ".zfb" else "—"))
        self._fields["Modified"].setText(
            datetime.fromtimestamp(rom.mtime).strftime("%Y-%m-%d %H:%M"))
        for slot, button in self.slot_buttons.items():
            button.setChecked(slot == rom.slot)
        self.export_button.setEnabled(rom.has_thumbnail)
        if pixmap is not None and not pixmap.isNull():
            self.art.setPixmap(pixmap.scaled(
                QtCore.QSize(self.WIDTH - 40, 290), KEEP_ASPECT, SMOOTH_TRANSFORM))
        else:
            self.art.setText("no artwork")

    def show_multi(self, roms: list[wc.RomEntry]) -> None:
        self._rom = None
        self.set_open(True)
        self.art.setText(f"{len(roms)} selected")
        self.art.setPixmap(QtGui.QPixmap())
        self.title.setText(f"{len(roms)} ROMs")
        total = sum(rom.size for rom in roms)
        self._fields["File"].setText("—")
        self._fields["Size"].setText(wc.human_size(total))
        self._fields["Format"].setText("mixed")
        self._fields["Core"].setText("—")
        self._fields["Modified"].setText("—")
        for button in self.slot_buttons.values():
            button.setChecked(False)


# ---------------------------------------------------------------------------
# Device / multicore / rescue pages
# ---------------------------------------------------------------------------


class ToolCard(QtWidgets.QFrame):
    """A single task, presented as a card instead of a menu item."""

    triggered = Signal()

    def __init__(self, title: str, body: str, action_text: str,
                 tone: str = "normal", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toolCard")
        self.setProperty("tone", tone)
        self.setMinimumHeight(132)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(7)

        layout.addWidget(label(title, "toolTitle", wrap=True))
        body_label = label(body, "toolBody", wrap=True)
        layout.addWidget(body_label, 1)

        row = QtWidgets.QHBoxLayout()
        self.button = QtWidgets.QPushButton(action_text)
        self._action_text = action_text
        self.button.setCursor(POINTING_HAND)
        if tone == "primary":
            self.button.setObjectName("primary")
        elif tone == "danger":
            self.button.setObjectName("danger")
        self.button.clicked.connect(self.triggered)
        row.addWidget(self.button)
        row.addStretch(1)

        self.choice = QtWidgets.QComboBox()
        self.choice.setVisible(False)
        row.addWidget(self.choice)
        layout.addLayout(row)

    def set_choices(self, items: list[str], placeholder: str = "") -> None:
        self.choice.clear()
        if not items:
            self.choice.setVisible(False)
            self.button.setEnabled(False)
            self.button.setText(placeholder or "Unavailable")
            return
        self.choice.addItems(items)
        self.choice.setVisible(True)
        self.button.setEnabled(True)
        self.button.setText(self._action_text)

    def selected(self) -> str:
        return self.choice.currentText()


class CardPage(QtWidgets.QWidget):
    """Scrolling page of tool cards with a heading."""

    def __init__(self, title: str, subtitle: str,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setObjectName("header")
        head = QtWidgets.QVBoxLayout(header)
        head.setContentsMargins(22, 16, 22, 14)
        head.setSpacing(1)
        head.addWidget(label(title, "pageTitle"))
        head.addWidget(label(subtitle, "pageSubtitle", wrap=True))
        outer.addWidget(header)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(qenum(QtWidgets.QFrame, "Shape", "NoFrame"))
        holder = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(holder)
        self.grid.setContentsMargins(22, 18, 22, 22)
        self.grid.setSpacing(14)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        self._row = 0
        self._column = 0
        self._columns = 3

    def add(self, card: ToolCard) -> ToolCard:
        self.grid.addWidget(card, self._row, self._column)
        self._column += 1
        if self._column >= self._columns:
            self._column = 0
            self._row += 1
        return card

    def add_section(self, title: str) -> None:
        if self._column:
            self._column = 0
            self._row += 1
        heading = label(title.upper(), "sectionLabel")
        self.grid.addWidget(heading, self._row, 0, 1, self._columns)
        self._row += 1


# ---------------------------------------------------------------------------
# Sheets and command palette
# ---------------------------------------------------------------------------


class Sheet(QtWidgets.QDialog):
    """Styled replacement for QMessageBox, used only where an answer matters."""

    def __init__(self, title: str, body: str, parent: QtWidgets.QWidget | None = None,
                 confirm: str = "", danger: bool = False, cancel: str = "Cancel") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        frame = QtWidgets.QFrame()
        frame.setObjectName("sheet")
        outer.addWidget(frame)

        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(label(title, "sheetTitle", wrap=True))
        layout.addWidget(label(body, "sheetBody", wrap=True))

        self.extra = QtWidgets.QVBoxLayout()
        layout.addLayout(self.extra)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        if confirm:
            cancel_button = QtWidgets.QPushButton(cancel)
            cancel_button.clicked.connect(self.reject)
            buttons.addWidget(cancel_button)
            go = QtWidgets.QPushButton(confirm)
            go.setObjectName("danger" if danger else "primary")
            go.setDefault(True)
            go.clicked.connect(self.accept)
            buttons.addWidget(go)
        else:
            close = QtWidgets.QPushButton("Close")
            close.setObjectName("primary")
            close.setDefault(True)
            close.clicked.connect(self.accept)
            buttons.addWidget(close)
        layout.addLayout(buttons)


class CommandPalette(QtWidgets.QDialog):
    """Ctrl+K: every action in the app, reachable by typing its name.

    This is what replaces the six nested menus the original carried.
    """

    def __init__(self, commands: list[tuple[str, str, Callable[[], None]]],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setWindowTitle("Commands")
        self._commands = commands

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QtWidgets.QFrame()
        frame.setObjectName("sheet")
        outer.addWidget(frame)

        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(6)

        self.input = QtWidgets.QLineEdit()
        self.input.setObjectName("paletteInput")
        self.input.setPlaceholderText("Type a command…")
        self.input.textChanged.connect(self._filter)
        self.input.returnPressed.connect(self._run)
        layout.addWidget(self.input)

        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setMinimumHeight(320)
        self.list.itemActivated.connect(lambda _: self._run())
        self.list.itemClicked.connect(lambda _: self._run())
        layout.addWidget(self.list)

        self._filter("")

    def _filter(self, text: str) -> None:
        terms = text.lower().split()
        self.list.clear()
        for title, group, handler in self._commands:
            haystack = f"{title} {group}".lower()
            if all(term in haystack for term in terms):
                item = QtWidgets.QListWidgetItem(f"{title}    ·  {group}")
                item.setData(wc.ROLE_USER, handler)
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        handler = item.data(wc.ROLE_USER)
        self.accept()
        if callable(handler):
            QtCore.QTimer.singleShot(0, handler)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key in (KEY_UP, KEY_DOWN):
            row = self.list.currentRow() + (1 if key == KEY_DOWN else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
            return
        if key == KEY_ESCAPE:
            self.reject()
            return
        super().keyPressEvent(event)


class PreferencesSheet(Sheet):
    def __init__(self, config: wc.Config, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(
            "Preferences",
            "Settings are stored per user; pass --portable to keep them beside the app.",
            parent, confirm="Save",
        )
        self.config = config

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)

        self.library = QtWidgets.QLineEdit(config.local_library())
        self.library.setPlaceholderText("Optional folder mirroring your card")
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.library, 1)
        row.addWidget(browse)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        form.addRow("Local library", holder)

        self.theme = QtWidgets.QComboBox()
        self.theme.addItems(["auto", "dark", "light"])
        self.theme.setCurrentText(config.get("theme"))
        form.addRow("Theme", self.theme)

        self.poll = QtWidgets.QSpinBox()
        self.poll.setRange(1, 30)
        self.poll.setSuffix(" s")
        self.poll.setValue(config.get_int("poll_seconds", 2))
        form.addRow("Look for cards every", self.poll)

        self.extra.addLayout(form)

        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        for key, text in (
            ("download_thumbnails", "Download box art automatically"),
            ("overwrite_thumbnails", "Overwrite artwork that already exists"),
            ("use_trash", "Deleted ROMs go to the Wolfpole bin (undoable)"),
            ("confirm_delete", "Ask before deleting"),
            ("auto_rebuild", "Rebuild the ROM index automatically after changes"),
            ("network", "Allow network access"),
        ):
            box = QtWidgets.QCheckBox(text)
            box.setChecked(config.get_bool(key))
            self.checks[key] = box
            self.extra.addWidget(box)

    def _browse(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose your local library", self.library.text())
        if folder:
            self.library.setText(folder)

    def apply(self) -> None:
        self.config.set("local_library", self.library.text().strip())
        self.config.set("theme", self.theme.currentText())
        self.config.set("poll_seconds", self.poll.value())
        for key, box in self.checks.items():
            self.config.set(key, int(box.isChecked()))


class AboutSheet(Sheet):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(
            f"{APP_NAME} {APP_VERSION}",
            f"{APP_TAGLINE}.\n\nA rewrite of Madpole by faanJD, which forked Tadpole by "
            "Eric Goldstein, built on frogtool by tzlion. Thanks also to wikkiewikkie, "
            "Jason Grieves, and osaka on the RetroHandhelds Discord for the bootloader fix.",
            parent,
        )
        report = QtWidgets.QPlainTextEdit()
        report.setReadOnly(True)
        report.setMaximumHeight(190)
        report.setPlainText(self._diagnostics())
        self.extra.addWidget(report)

    @staticmethod
    def _diagnostics() -> str:
        paths = wc.resolve_paths()
        lines = [
            f"Qt binding : {QT_BINDING}",
            f"Settings   : {paths.config_file}",
            f"Log        : {paths.log_file}",
            "",
            "Modules:",
        ]
        for name in ("frogtool", "tadpole_functions", "multicore_functions", "mcoredata",
                     "sf2000ROM", "tadpoleConfig", "psutil", "requests"):
            ok = wc.optional_import(name) is not None
            lines.append(f"  {'ok     ' if ok else 'missing'}  {name}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class WolfpoleWindow(QtWidgets.QMainWindow):
    """The shell: sidebar, page stack, inspector, toasts, task tray.

    There is no menu bar and no modal progress dialog. Every operation runs in
    a worker, reports into the sidebar tray, and finishes with a toast.
    """

    def __init__(self, paths: wc.AppPaths, config: wc.Config,
                 palette: dict[str, str], initial_drive: str | None = None) -> None:
        super().__init__()
        self.paths = paths
        self.config = config
        self.palette_colours = palette

        self.runner = wc.JobRunner(self)
        self.cache = wc.ThumbnailCache(self.runner, parent=self)
        self.trash = wc.Trash(paths)
        self.catalogues = wc.Catalogues(paths, config)

        self.drives: list[wc.DriveInfo] = []
        self.shortcuts: wc.ShortcutStore | None = None
        self.firmware: dict[str, Any] = {}
        self.themes: dict[str, str] = {}
        self.music: dict[str, str] = {}
        self.bootlogos: dict[str, str] = {}
        self.system = (config.get("last_system") or wc.known_systems()[0])
        self._undo_count = 0
        self.scope_all = False
        self._sizes: dict[str, int] = {}
        self._counts: dict[str, int] = {}

        self.setWindowTitle(f"{APP_NAME} — {APP_TAGLINE}")
        self.setWindowIcon(self._icon())
        self.resize(1440, 880)
        self.setMinimumSize(1060, 640)
        self.setAcceptDrops(True)

        self._build()
        self._wire()
        self._register_commands()
        self._install_shortcuts()

        config.restore_geometry(self)
        self.toasts = ToastHost(self)

        self.watcher = wc.DriveWatcher(config, self)
        self.watcher.drivesChanged.connect(self.on_drives_changed)
        self.watcher.start()

        self._load_catalogues()
        self.sidebar.select_system(self.system)
        self._show_no_card()

        if initial_drive:
            QtCore.QTimer.singleShot(500, lambda: self.select_drive(initial_drive))

    # -- construction ------------------------------------------------------
    def _icon(self) -> QtGui.QIcon:
        for name in ("wolfpole.ico", "wolfpole.png", "madpole.ico", "tadpole.ico"):
            candidate = BASE_DIR / name
            if candidate.exists():
                return QtGui.QIcon(str(candidate))
        return QtGui.QIcon()

    def _build(self) -> None:
        central = QtWidgets.QWidget()
        shell = QtWidgets.QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.sidebar = Sidebar(self.palette_colours, self)
        shell.addWidget(self.sidebar)

        self.model = wc.RomModel(self.cache, self)
        self.model.set_show_thumbnails(True)
        self.proxy = wc.RomFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(wc.RomModel.COL_NAME, wc.SORT_ASCENDING)

        self.pages = QtWidgets.QStackedWidget()
        self.library = LibraryPage(self.model, self.proxy, self.cache,
                                   self.palette_colours, self)
        self.library.zoom.setValue(self.config.get_int("thumbnail_scale", 100) or 100)
        self.library.set_zoom(self.library.zoom.value())
        self.library.set_view(self.config.get("view_mode", "grid") or "grid")
        # Restore the session: filter chip, scope and view come back as they were.
        saved_filter = self.config.get("filter_mode", "all") or "all"
        self.library.set_filter(saved_filter)
        self.proxy.set_mode(saved_filter)
        self.scope_all = self.config.get_bool("scope_all")
        self.library.scope_button.setChecked(self.scope_all)
        self.pages.addWidget(self.library)

        self.overview_page = OverviewPage(self.palette_colours, self)
        self.pages.addWidget(self.overview_page)

        self.builder_page = BuilderPage(self.palette_colours, self)
        self.pages.addWidget(self.builder_page)

        self.device_page = self._build_device_page()
        self.pages.addWidget(self.device_page)
        self.multicore_page = self._build_multicore_page()
        self.pages.addWidget(self.multicore_page)
        self.rescue_page = self._build_rescue_page()
        self.pages.addWidget(self.rescue_page)

        shell.addWidget(self.pages, 1)

        self.inspector = Inspector(self)
        shell.addWidget(self.inspector)

        self.setCentralWidget(central)

        self.activity = ActivityLog(self)
        self.addDockWidget(qenum(Qt, "DockWidgetArea", "BottomDockWidgetArea"),
                           self.activity)
        self.activity.hide()
        self.activity.visibilityChanged.connect(
            lambda visible: self.sidebar.activity_button.setChecked(visible))

        # Watch the open folder so edits made outside Wolfpole show up.
        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watch_timer = QtCore.QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.setInterval(1400)
        self._watch_timer.timeout.connect(self._folder_changed)
        self._watcher.directoryChanged.connect(lambda _: self._watch_timer.start())

    def _build_device_page(self) -> CardPage:
        page = CardPage("Device & firmware",
                        "Everything that writes to the card's system files. "
                        "Each card explains what it changes before it runs.")

        page.add_section("Firmware")
        self.card_firmware = page.add(ToolCard(
            "Install firmware", "Replace the SF2000 system files with a published "
            "build. ROMs and saves are left alone.", "Install", "primary"))
        self.card_firmware.set_choices([], "Loading…")
        self.card_firmware.triggered.connect(self._install_selected_firmware)

        card = page.add(ToolCard(
            "Identify firmware", "Read bisrv.asd and report which build this card "
            "is running.", "Detect"))
        card.triggered.connect(self.detect_firmware)

        card = page.add(ToolCard(
            "Build a fresh card", "For a card you have just formatted to FAT32. "
            "Installs the newest firmware onto an empty volume.", "Start"))
        card.triggered.connect(self.build_fresh_card)

        page.add_section("Look and sound")
        self.card_theme = page.add(ToolCard(
            "Theme", "Swap the home screen theme. This overwrites your shortcut "
            "icons, which you can set again afterwards.", "Apply"))
        self.card_theme.set_choices([], "Loading…")
        self.card_theme.triggered.connect(
            lambda: self.apply_theme_pack(self.card_theme.selected()))

        self.card_music = page.add(ToolCard(
            "Background music", "Replace the tune that loops on the home screen.",
            "Apply"))
        self.card_music.set_choices([], "Loading…")
        self.card_music.triggered.connect(
            lambda: self.change_music(self.card_music.selected()))

        self.card_logo = page.add(ToolCard(
            "Boot logo", "Patch the splash image shown while the console starts.",
            "Apply"))
        self.card_logo.set_choices([], "Loading…")
        self.card_logo.triggered.connect(
            lambda: self.change_boot_logo(self.card_logo.selected()))

        card = page.add(ToolCard(
            "Strip shortcut text", "Remove the captions under the four home-screen "
            "shortcuts, leaving just the artwork.", "Strip"))
        card.triggered.connect(self.strip_shortcut_text)

        card = page.add(ToolCard(
            "Local artwork or audio", "Use a theme ZIP, music file or logo image "
            "from your own computer instead of the online lists.", "Choose file…"))
        card.triggered.connect(self._local_asset)

        page.add_section("Patches and BIOS")
        card = page.add(ToolCard(
            "Battery patch", "Community patch that improves battery life and adds a "
            "low-power warning. Stock v1.6 and v1.71 only.", "Patch"))
        card.triggered.connect(self.battery_patch)

        card = page.add(ToolCard(
            "Bootloader patch", "Stops the bug that corrupts the card when files "
            "change. Apply once per console. Verified by checksum.", "Patch", "primary"))
        card.triggered.connect(self.bootloader_patch)

        card = page.add(ToolCard(
            "GBA BIOS", "Copy gba_bios.bin into place so Game Boy Advance titles "
            "boot correctly.", "Install"))
        card.triggered.connect(self.gba_bios_fix)

        page.add_section("Data")
        card = page.add(ToolCard(
            "Back up saves", "Zip every save file on the card to a folder on your "
            "computer. Do this before any rescue work.", "Back up", "primary"))
        card.triggered.connect(self.backup_saves)

        card = page.add(ToolCard(
            "Copy local library to card", "Mirror your local library folder onto the "
            "card, skipping files that are already up to date.", "Copy"))
        card.triggered.connect(self.copy_library_to_card)

        card = page.add(ToolCard(
            "Rebuild every system", "Regenerate the ROM index for all consoles. Use "
            "after editing the card outside Wolfpole.", "Rebuild"))
        card.triggered.connect(self.rebuild_everything)

        card = page.add(ToolCard(
            "Refresh online lists", "Re-download the firmware, theme, music and logo "
            "catalogues. They are cached for a day.", "Refresh"))
        card.triggered.connect(self.refresh_catalogues)
        return page

    def _build_multicore_page(self) -> CardPage:
        page = CardPage("Multicore",
                        "Tools for ROMs that run on an added emulator core rather "
                        "than the stock ones.")

        def legacy(title: str, body: str, action: str, module: str, attr: str) -> None:
            card = page.add(ToolCard(title, body, action))
            dialog_cls = wc.optional_import(module, attr)
            if dialog_cls is None:
                card.button.setEnabled(False)
                card.button.setText("Unavailable")
                card.setToolTip(f"{module} could not be imported: "
                                f"{wc.MISSING.get(f'{module}:{attr}', 'missing')}")
            else:
                card.triggered.connect(lambda cls=dialog_cls: self.run_legacy_dialog(cls))

        legacy("Add multicore ROMs", "Import ROMs and generate the stub files the "
               "console needs to hand them to a core.", "Open",
               "dialogs.MulticoreAddDialog", "MulticoreAddDialog")
        legacy("Create ZFB stubs", "Build stubs for multicore ROMs already sitting "
               "on the card.", "Open", "dialogs.MulticoreDialog", "MulticoreDialog")
        legacy("Create STUBS", "Generate the stub set for a multicore install.",
               "Open", "dialogs.MulticoreStubsDialog", "MulticoreStubsDialog")
        legacy("Multicore options", "Edit per-core options stored on the card.",
               "Open", "dialogs.MulticoreOptDialog", "MulticoreOptDialog")
        legacy("Stock sections editor", "Rename and rearrange the stock console "
               "sections on the home screen.", "Open",
               "dialogs.SectionEdit", "SectionEdit")

        card = page.add(ToolCard(
            "Change core of selection", "Point the ROMs selected in the library at a "
            "different emulator core.", "Change"))
        card.triggered.connect(self.change_core)

        available = wc.optional_import("multicore_functions") is not None
        card = page.add(ToolCard(
            "Rebuild multicore list", "Rescan the multicore folders and rebuild the "
            "index the console reads.", "Rebuild"))
        card.button.setEnabled(available)
        card.triggered.connect(lambda: self.rebuild_multicore(False))

        card = page.add(ToolCard(
            "Rebuild in arcade mode", "Same rebuild, but files are registered under "
            "the arcade section.", "Rebuild"))
        card.button.setEnabled(available)
        card.triggered.connect(lambda: self.rebuild_multicore(True))
        return page

    def _build_rescue_page(self) -> CardPage:
        page = CardPage("Rescue",
                        "If the console will not start, work down this page. "
                        "Back up your saves before anything destructive.")

        card = page.add(ToolCard(
            "Try the light fix first", "Writes and deletes a scratch file in the "
            "bios folder, which shuffles the allocation table and often clears the "
            "boot bug. Nothing is erased.", "Run", "primary"))
        card.triggered.connect(self.fix_boot_light)

        card = page.add(ToolCard(
            "Check this card for faults", "Looks for a missing or truncated firmware, "
            "absent folders, stale ROM indexes and pending updates.", "Run check",
            "primary"))
        card.triggered.connect(self.run_doctor)

        card = page.add(ToolCard(
            "Deep card test", "Writes and reads back samples across the whole card. "
            "This is how you catch a counterfeit or a card that is wearing out.",
            "Test card"))
        card.triggered.connect(self.run_health_check)

        card = page.add(ToolCard(
            "Apply the bootloader patch", "The permanent fix. Needs a working boot, "
            "so run it once the console starts again.", "Patch"))
        card.triggered.connect(self.bootloader_patch)

        card = page.add(ToolCard(
            "Create missing folders", "Recreate the console and system folders the "
            "firmware expects, without touching anything already there.", "Repair"))
        card.triggered.connect(self.repair_structure)

        card = page.add(ToolCard(
            "Safely eject", "Flush every pending write and unmount the card. Pulling "
            "a card mid-write is the most common way to corrupt one.", "Eject"))
        card.triggered.connect(self.safe_eject)

        card = page.add(ToolCard(
            "Full rebuild", "Reformat and reinstall. This erases every ROM and save "
            "on the card.", "What this involves", "danger"))
        card.triggered.connect(self.fix_boot_full)

        page.add_section("Housekeeping")
        card = page.add(ToolCard(
            "Wolfpole bin", "Deleted ROMs wait here until you empty it.",
            "Empty bin…", "danger"))
        card.triggered.connect(self.empty_trash)

        card = page.add(ToolCard(
            "Open the log", "Rotating log of everything Wolfpole has done. Useful "
            "when asking for help.", "Open"))
        card.triggered.connect(lambda: wc.open_in_file_manager(self.paths.log_file))

        card = page.add(ToolCard(
            "Bootloader bug explained", "Background reading on why SF2000 cards "
            "break, and how the community fix works.", "Open page"))
        card.triggered.connect(lambda: webbrowser.open(BOOTLOADER_DOC_URL))

        card = page.add(ToolCard(
            "RetroHandhelds Discord", "Where the SF2000 community answers questions.",
            "Open"))
        card.triggered.connect(lambda: webbrowser.open(DISCORD_URL))

        card = page.add(ToolCard(
            "About Wolfpole", "Version, credits and which optional modules loaded.",
            "Show"))
        card.triggered.connect(lambda: qexec(AboutSheet(self)))
        return page

    def _wire(self) -> None:
        self.sidebar.driveClicked.connect(self.show_drive_menu)
        self.sidebar.activityToggled.connect(self.activity.setVisible)
        self.sidebar.systemSelected.connect(self.on_system_changed)
        self.sidebar.pageSelected.connect(self.show_page)

        self.library.searchChanged.connect(self.on_search)
        self.library.viewChanged.connect(self.on_view_changed)
        self.library.sortRequested.connect(
            lambda column: self.proxy.sort(column, wc.SORT_ASCENDING))
        self.library.zoomChanged.connect(self.on_zoom)
        self.library.selectionChanged.connect(self.on_selection_changed)
        self.library.activated.connect(self.on_activated)
        self.library.contextRequested.connect(self.show_context_menu)
        self.library.addRequested.connect(self.add_roms)
        self.library.artworkRequested.connect(self.add_artwork)
        self.library.rebuildRequested.connect(lambda: self.rebuild_current())
        self.library.dock.assignRequested.connect(self.assign_selection_to_slot)
        self.library.dock.clearRequested.connect(self.clear_slot)
        self.library.filterChanged.connect(self.on_filter_changed)
        self.library.scopeChanged.connect(self.on_scope_changed)
        self.library.toolsRequested.connect(self.show_tools_menu)
        self.library.grid.artworkDropped.connect(self.apply_dropped_artwork)
        self.library.grid.romsDropped.connect(self.copy_roms_in)

        self.overview_page.systemPicked.connect(self.on_system_changed)
        self.overview_page.action.connect(self.on_overview_action)

        self.builder_page.refreshRequested.connect(self.refresh_build_targets)
        self.builder_page.checkRequested.connect(self.run_preflight)
        self.builder_page.healthRequested.connect(self.run_health_check)
        self.builder_page.buildRequested.connect(self.start_build)

        self.inspector.replaceArt.connect(self.replace_artwork)
        self.inspector.exportArt.connect(self.export_artwork)
        self.inspector.reveal.connect(lambda rom: wc.open_in_file_manager(rom.path))
        self.inspector.rename.connect(self.rename_rom)
        self.inspector.remove.connect(lambda rom: self.delete_roms([rom]))
        self.inspector.assignSlot.connect(self.set_slot)

        self.model.slotChanged.connect(self.set_slot)
        self.model.nameChanged.connect(self.apply_rename)
        self.cache.ready.connect(self._art_arrived)

    def _install_shortcuts(self) -> None:
        for keys, handler in (
            ("Ctrl+K", self.show_palette),
            ("Ctrl+P", self.show_palette),
            ("Ctrl+F", lambda: (self.library.search.setFocus(),
                                self.library.search.selectAll())),
            ("Ctrl+O", self.add_roms),
            ("Ctrl+T", self.add_artwork),
            ("F5", lambda: self.rebuild_current()),
            ("Shift+F5", self.rebuild_everything),
            ("Ctrl+R", self.reload_roms),
            ("Del", self.delete_selection),
            ("Ctrl+Z", self.undo_delete),
            ("Ctrl+,", self.show_preferences),
            ("Ctrl+A", lambda: self.library.current_view().selectAll()),
            ("Ctrl+Q", self.close),
            ("Escape", lambda: self.inspector.set_open(False)),
            ("F2", self.rename_selection),
            ("Ctrl+C", self.copy_paths),
            ("Ctrl+Shift+F", lambda: self.library.scope_button.setChecked(
                not self.library.scope_button.isChecked())),
            ("Ctrl+L", lambda: self.activity.setVisible(not self.activity.isVisible())),
            ("Ctrl+B", lambda: self.show_page("build")),
            ("Ctrl+D", self.find_duplicates),
            ("Ctrl+E", self.safe_eject),
            ("Ctrl+1", lambda: self.library.grid_button.click()),
            ("Ctrl+2", lambda: self.library.list_button.click()),
        ):
            shortcut = wc.QShortcut(QtGui.QKeySequence(keys), self)
            shortcut.activated.connect(handler)

    def _register_commands(self) -> None:
        self.commands: list[tuple[str, str, Callable[[], None]]] = [
            ("Add ROMs…", "Library", self.add_roms),
            ("Add artwork", "Library", self.add_artwork),
            ("Rebuild this system", "Library", lambda: self.rebuild_current()),
            ("Rebuild every system", "Library", self.rebuild_everything),
            ("Reload folder", "Library", self.reload_roms),
            ("Delete selection", "Library", self.delete_selection),
            ("Undo delete", "Library", self.undo_delete),
            ("Export ROM list to CSV…", "Library", self.export_csv),
            ("Switch to grid view", "View", lambda: self.on_view_changed("grid")),
            ("Switch to list view", "View", lambda: self.on_view_changed("list")),
            ("Install firmware…", "Device", self._install_selected_firmware),
            ("Identify firmware", "Device", self.detect_firmware),
            ("Apply theme", "Device", lambda: self.apply_theme_pack(self.card_theme.selected())),
            ("Change background music", "Device",
             lambda: self.change_music(self.card_music.selected())),
            ("Change boot logo", "Device",
             lambda: self.change_boot_logo(self.card_logo.selected())),
            ("Strip shortcut text", "Device", self.strip_shortcut_text),
            ("Battery patch", "Device", self.battery_patch),
            ("Bootloader patch", "Device", self.bootloader_patch),
            ("Install GBA BIOS", "Device", self.gba_bios_fix),
            ("Back up saves…", "Device", self.backup_saves),
            ("Copy local library to card", "Device", self.copy_library_to_card),
            ("Build a fresh SD card", "Device", self.build_fresh_card),
            ("Refresh online lists", "Device", self.refresh_catalogues),
            ("Change core of selection", "Multicore", self.change_core),
            ("Rebuild multicore list", "Multicore", lambda: self.rebuild_multicore(False)),
            ("Rebuild multicore list (arcade)", "Multicore",
             lambda: self.rebuild_multicore(True)),
            ("Light boot fix", "Rescue", self.fix_boot_light),
            ("Full rebuild guidance", "Rescue", self.fix_boot_full),
            ("Empty the Wolfpole bin", "Rescue", self.empty_trash),
            ("Open the log file", "Rescue", lambda: wc.open_in_file_manager(self.paths.log_file)),
            ("Tidy up ROM names…", "Bulk", self.tidy_names),
            ("Move selection to another system…", "Bulk", self.move_to_system),
            ("Find duplicate ROMs", "Bulk", self.find_duplicates),
            ("Export shortcut layout…", "Bulk", self.export_shortcuts),
            ("Import shortcut layout…", "Bulk", self.import_shortcuts),
            ("Build a card", "Device", lambda: self.show_page("build")),
            ("Check this card for faults", "Device", self.run_doctor),
            ("Deep-test this card", "Device", self.run_health_check),
            ("Create missing folders", "Device", self.repair_structure),
            ("Safely eject the card", "Device", self.safe_eject),
            ("Show the activity log", "App",
             lambda: self.activity.setVisible(True)),
            ("Preferences…", "App", self.show_preferences),
            ("About Wolfpole", "App", lambda: qexec(AboutSheet(self))),
        ]
        for system in wc.known_systems():
            self.commands.append(
                (f"Go to {wc.system_label(system)}", "Systems",
                 lambda s=system: self.on_system_changed(s)))
        for key, title in (("device", "Device & firmware"), ("multicore", "Multicore"),
                           ("rescue", "Rescue")):
            self.commands.append(
                (f"Open {title}", "Navigate", lambda k=key: self.show_page(k)))

    # -- conversation helpers ---------------------------------------------
    def notify(self, text: str, tone: str = "info",
               action: tuple[str, Callable[[], None]] | None = None) -> None:
        self.toasts.show(text, tone, action)
        if hasattr(self, "activity"):
            self.activity.add(text, tone)

    def ask(self, title: str, body: str, confirm: str, danger: bool = False) -> bool:
        return bool(qexec(Sheet(title, body, self, confirm=confirm, danger=danger)))

    def tell(self, title: str, body: str) -> None:
        qexec(Sheet(title, body, self))

    # -- jobs --------------------------------------------------------------
    def run_job(self, title: str, fn: Callable[..., Any], *args: Any,
                on_result: Callable[[Any], None] | None = None,
                cancellable: bool = True, needs_handle: bool = False,
                quiet: bool = False, **kwargs: Any) -> wc.Job:
        """Run work in a pool thread, reporting into the sidebar tray."""
        handle = self.sidebar.tray.start(title, cancellable)
        call_args = (*args, handle) if needs_handle else args

        def failed(message: str) -> None:
            self.notify(f"{title} failed", "bad",
                        ("Details", lambda m=message: self.tell(title, m)))

        job = self.runner.submit(
            fn, *call_args,
            on_result=on_result,
            on_error=failed,
            on_progress=handle.on_progress,
            on_message=handle.on_message,
            on_cancelled=lambda: self.notify(f"{title} cancelled"),
            on_done=lambda: self.sidebar.tray.finish(handle),
            **kwargs,
        )
        handle.bind(job)
        if not quiet:
            log.info("Started: %s", title)
        return job

    def run_legacy_dialog(self, dialog_cls: type) -> None:
        self.config.cDir = self.drive if self.has_card else ""
        self.config.cCon = self.system
        try:
            qexec(dialog_cls(self.config))
        except Exception as exc:  # noqa: BLE001
            log.exception("Legacy dialog failed")
            self.tell("That dialog could not run", str(exc))
            return
        self.after_change()

    # -- drives ------------------------------------------------------------
    @property
    def drive(self) -> str:
        current = getattr(self, "_drive", "")
        return current or NO_DRIVE

    @property
    def has_card(self) -> bool:
        return self.drive not in ("", NO_DRIVE)

    def current_drive_info(self) -> wc.DriveInfo | None:
        for info in self.drives:
            if info.mountpoint == self.drive:
                return info
        return None

    def on_drives_changed(self, drives: list[wc.DriveInfo]) -> None:
        self.drives = drives
        known = {info.mountpoint for info in drives}
        if self.has_card and self.drive in known:
            self.sidebar.chip.set_drive(self.current_drive_info())
            return
        if not drives:
            self._drive = ""
            self.sidebar.chip.set_drive(None)
            self._show_no_card()
            return
        remembered = self.config.get("last_drive", "")
        pick = next((i for i in drives if i.mountpoint == remembered), drives[0])
        self.select_drive(pick.mountpoint)
        self.notify(f"Card detected: {pick.mountpoint}", "good")

    def show_drive_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        if not self.drives:
            action = menu.addAction("No cards detected")
            action.setEnabled(False)
        for info in self.drives:
            action = menu.addAction(info.describe())
            action.setCheckable(True)
            action.setChecked(info.mountpoint == self.drive)
            action.triggered.connect(
                lambda _=False, m=info.mountpoint: self.select_drive(m))
        menu.addSeparator()
        menu.addAction("Preferences…", self.show_preferences)
        popup = getattr(menu, "exec", None) or menu.exec_
        popup(self.sidebar.chip.mapToGlobal(
            QtCore.QPoint(0, self.sidebar.chip.height() + 4)))

    def select_drive(self, mountpoint: str) -> None:
        if not any(info.mountpoint == mountpoint for info in self.drives):
            self.notify(f"{mountpoint} is not a detected card", "bad")
            return
        self._drive = mountpoint
        self.config.set("last_drive", mountpoint)
        self.cache.clear()
        self.shortcuts = wc.ShortcutStore(mountpoint)
        self.sidebar.chip.set_drive(self.current_drive_info())
        self.sidebar.set_enabled_for_card(True)
        self.sidebar.select_system(self.system)
        self.pages.setCurrentWidget(self.library)
        self.refresh_counts()
        self.reload_roms()
        self.builder_page.set_firmware(self._firmware_titles())

    def _show_no_card(self) -> None:
        self.sidebar.set_enabled_for_card(False)
        self.sidebar.set_counts({})
        self.model.set_roms([])
        self.pages.setCurrentWidget(self.library)
        self.library.title.setText("No card")
        self.library.subtitle.setText("waiting for an SF2000 SD card")
        self.library.show_content(False)
        self.library.empty.set(
            "Insert an SF2000 card",
            "Wolfpole watches for cards in the background and opens the library "
            "as soon as one appears. If you keep a local copy of your card on this "
            "computer, point Wolfpole at it in Preferences and you can work offline.",
            ("Open preferences", self.show_preferences),
        )
        self.inspector.set_open(False)

    def refresh_counts(self) -> None:
        if not self.has_card:
            return
        self.run_job("Counting ROMs", wc.count_roms, self.drive,
                     on_result=self.sidebar.set_counts, quiet=True)

    # -- navigation --------------------------------------------------------
    def show_page(self, key: str) -> None:
        widget = {"overview": self.overview_page, "device": self.device_page,
                  "multicore": self.multicore_page, "build": self.builder_page,
                  "rescue": self.rescue_page}.get(key)
        if widget is None:
            return
        self.pages.setCurrentWidget(widget)
        self.sidebar.select_page(key)
        self.inspector.set_open(False)
        if key == "overview":
            self.refresh_overview()
        elif key == "build":
            self.refresh_build_targets()
            self.builder_page.set_firmware(self._firmware_titles())

    def on_system_changed(self, system: str) -> None:
        self.system = system
        self.config.set("last_system", system)
        self.sidebar.select_system(system)
        self.pages.setCurrentWidget(self.library)
        self.library.search.clear()
        self.reload_roms()

    def on_view_changed(self, kind: str) -> None:
        self.library.set_view(kind)
        self.config.set("view_mode", kind)
        self.library.show_content(self.proxy.rowCount() > 0)

    def on_zoom(self, value: int) -> None:
        self.config.set("thumbnail_scale", value)
        self.library.set_zoom(value)

    def on_search(self, text: str) -> None:
        self.proxy.set_query(text)
        self._update_subtitle()
        self.library.show_content(self.proxy.rowCount() > 0)
        if self.proxy.rowCount() == 0 and self.model.rowCount():
            self.library.empty.set(
                "Nothing matches",
                f"No ROM in {self.system} matches “{text}”.",
                ("Clear search", self.library.search.clear),
            )

    # -- library -----------------------------------------------------------
    def reload_roms(self) -> None:
        if not self.has_card:
            self._show_no_card()
            return
        drive = self.drive
        scope_all = self.scope_all
        systems = wc.known_systems() if scope_all else [self.system]
        slot_maps = {name: (self.shortcuts.slots(name) if self.shortcuts else {})
                     for name in systems}

        def body(ctx: wc.JobContext) -> list[wc.RomEntry]:
            roms = (wc.scan_all_systems(ctx, drive) if scope_all
                    else wc.scan_roms(ctx, drive, self.system))
            self._apply_slots(roms, drive, slot_maps)
            return roms

        title = "Everything" if scope_all else wc.system_label(self.system)
        self.library.title.setText(title)
        self.library.subtitle.setText("reading the card…")
        self.run_job("Reading the card" if scope_all else f"Reading {self.system}",
                     body, on_result=self._roms_loaded, quiet=True)
        self._watch_current_folder()

    @staticmethod
    def _apply_slots(roms: list[wc.RomEntry], drive: str,
                     slot_maps: dict[str, dict[str, int]]) -> None:
        tf = wc.optional_import("tadpole_functions")
        getter = getattr(tf, "getGameShortcutPosition", None) if tf else None
        for rom in roms:
            slot = slot_maps.get(rom.system, {}).get(rom.filename, 0)
            if not slot and callable(getter):
                try:
                    slot = int(getter(drive, rom.system, rom.filename) or 0)
                except Exception:  # noqa: BLE001 - no index yet is normal
                    slot = 0
            rom.slot = slot if 0 <= slot <= 4 else 0

    def _roms_loaded(self, roms: list[wc.RomEntry]) -> None:
        self.model.set_roms(roms)
        self.library.delegate.show_system = self.scope_all
        self._update_subtitle()
        self.library.show_content(bool(roms) and self.proxy.rowCount() > 0)
        if not roms:
            self.library.empty.set(
                "Nothing here yet" if self.scope_all
                else f"No ROMs in {wc.system_label(self.system)}",
                "Drag ROM files onto this window, or use Add ROMs. Wolfpole will "
                "offer to fetch artwork once they are copied.",
                ("Add ROMs…", self.add_roms),
            )
        elif self.proxy.rowCount() == 0:
            self.library.empty.set(
                "Nothing matches this filter",
                "No ROM here fits the current filter and search.",
                ("Show everything", lambda: (self.library.set_filter("all"),
                                             self.on_filter_changed("all"),
                                             self.library.search.clear())),
            )
        self.library.dock.setVisible(not self.scope_all)
        self.refresh_slot_dock()
        self.on_selection_changed()
        self.sidebar.chip.set_drive(self.current_drive_info())
        self._warn_if_full()

    def _update_subtitle(self) -> None:
        total = self.model.rowCount()
        shown = self.proxy.rowCount()
        size = wc.human_size(sum(rom.size for rom in self.model.roms()))
        text = f"{total} ROMs · {size}" if total else "empty"
        if shown != total:
            text = f"{shown} of {total} shown · {size}"
        self.library.title.setText(
            "Everything" if self.scope_all else wc.system_label(self.system))
        self.library.subtitle.setText(f"{text}  ·  {self.drive}")

    def after_change(self, rebuild: bool = True) -> None:
        self.refresh_counts()
        if rebuild and self.config.get_bool("auto_rebuild") and self.has_card:
            self.rebuild_current()
        else:
            self.reload_roms()

    # -- selection ---------------------------------------------------------
    def selected(self) -> list[wc.RomEntry]:
        return self.library.selected_roms()

    def on_selection_changed(self) -> None:
        roms = self.selected()
        if not roms:
            self.inspector.show_rom(None, None)
            return
        if len(roms) > 1:
            self.inspector.show_multi(roms)
            return
        rom = roms[0]
        if not rom.core_checked:
            rom.core = wc.detect_core(rom.path)
            rom.core_checked = True
        pixmap = self.cache.pixmap(rom.path) if rom.has_thumbnail else None
        self.inspector.show_rom(rom, pixmap)

    def _art_arrived(self, path: str) -> None:
        roms = self.selected()
        if len(roms) == 1 and roms[0].path == path:
            self.inspector.show_rom(roms[0], self.cache.pixmap(path))
        self.refresh_slot_dock()

    def on_activated(self, rom: Any) -> None:
        if isinstance(rom, wc.RomEntry):
            self.replace_artwork(rom)

    def show_context_menu(self, position: QtCore.QPoint) -> None:
        roms = self.selected()
        if not roms:
            return
        menu = QtWidgets.QMenu(self)
        if len(roms) == 1:
            rom = roms[0]
            menu.addAction("Replace artwork…", lambda: self.replace_artwork(rom))
            if rom.has_thumbnail:
                menu.addAction("Export artwork…", lambda: self.export_artwork(rom))
            menu.addAction("Rename…", lambda: self.rename_rom(rom))
            menu.addAction("Show in file manager",
                           lambda: wc.open_in_file_manager(rom.path))
            menu.addAction("Copy path",
                           lambda: QtWidgets.QApplication.clipboard().setText(rom.path))
            slots = menu.addMenu("Shortcut slot")
            for slot in range(5):
                text = "None" if slot == 0 else f"Slot {slot}"
                slots.addAction(text, lambda s=slot, r=rom: self.set_slot(r, s))
            menu.addSeparator()
        menu.addAction("Change core…", self.change_core)
        menu.addAction(f"Delete {len(roms)} ROM(s)", self.delete_selection)
        popup = getattr(menu, "exec", None) or menu.exec_
        popup(position)

    # -- shortcut slots ----------------------------------------------------
    def refresh_slot_dock(self) -> None:
        by_slot = {rom.slot: rom for rom in self.model.roms() if rom.slot}
        for slot, tile in self.library.dock.tiles.items():
            rom = by_slot.get(slot)
            pixmap = self.cache.pixmap(rom.path) if rom is not None and rom.has_thumbnail else None
            tile.set_rom(rom, pixmap)

    def assign_selection_to_slot(self, slot: int) -> None:
        roms = self.selected()
        if len(roms) != 1:
            self.notify("Select one ROM first, then click a slot")
            return
        self.set_slot(roms[0], slot)

    def clear_slot(self, slot: int) -> None:
        for rom in self.model.roms():
            if rom.slot == slot:
                self.set_slot(rom, 0)
                return

    def set_slot(self, rom: wc.RomEntry, slot: int) -> None:
        """Write a shortcut slot, moving it off whoever held it."""
        if not self.has_card:
            return
        displaced = [r for r in self.model.roms()
                     if r is not rom and slot and r.slot == slot]
        for other in displaced:
            other.slot = 0
        rom.slot = slot

        multicore_name = self._multicore_name(rom)
        if self.shortcuts is not None:
            self.shortcuts.set_slot(self.system, slot,
                                    rom.filename if multicore_name else "")

        tf = wc.optional_import("tadpole_functions")
        setter = getattr(tf, "changeGameShortcut", None) if tf else None
        if callable(setter) and slot:
            try:
                setter(self.drive, self.system, slot - 1,
                       multicore_name or rom.filename)
            except Exception as exc:  # noqa: BLE001
                log.error("changeGameShortcut failed: %s", exc)
                self.notify("The shortcut could not be written", "bad",
                            ("Details", lambda e=str(exc): self.tell("Shortcut", e)))
                return

        self.model.layoutChanged.emit()
        self.refresh_slot_dock()
        self.on_selection_changed()
        if slot and displaced:
            self.notify(f"Slot {slot}: {displaced[0].name} → {rom.name}", "good")
        elif slot:
            self.notify(f"{rom.name} is now shortcut {slot}", "good")
        else:
            self.notify(f"{rom.name} removed from the shortcuts")

    @staticmethod
    def _multicore_name(rom: wc.RomEntry) -> str:
        if rom.ext != ".zfb":
            return ""
        mc = wc.optional_import("mcoredata")
        getter = getattr(mc, "getZfbData", None) if mc else None
        if not callable(getter):
            return ""
        try:
            data = getter(rom.path)
        except Exception as exc:  # noqa: BLE001
            log.debug("getZfbData failed: %s", exc)
            return ""
        if data and len(data) >= 3 and data[2]:
            return f"{data[0]};{data[1]}"
        return ""

    # -- ROM operations ----------------------------------------------------
    def add_roms(self) -> None:
        if not self._need_card():
            return
        if self.system == "ARCADE":
            patterns = "Arcade ROM sets (*.zip)"
        else:
            patterns = "ROM files (" + " ".join(f"*{e}" for e in wc.ROM_EXTENSIONS) + ")"
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Choose ROMs", "", patterns)
        if files:
            self.copy_roms_in(files)

    def copy_roms_in(self, files: Iterable[str]) -> None:
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            return
        drive, system = self.drive, self.system
        needed = sum(os.path.getsize(f) for f in files)
        free, _ = wc.free_space(drive)
        if free and needed > free * 0.98:
            self.tell("Not enough space",
                      f"Those ROMs need {wc.human_size(needed)} but only "
                      f"{wc.human_size(free)} is free on {drive}.")
            return

        target = Path(drive) / system / ("bin" if system == "ARCADE" else "")
        tf = wc.optional_import("tadpole_functions")
        make_zfb = getattr(tf, "createZFBFile", None) if tf else None

        def body(ctx: wc.JobContext) -> tuple[int, list[str]]:
            target.mkdir(parents=True, exist_ok=True)
            copied, failed = 0, []
            total = len(files)
            for i, path in enumerate(files):
                ctx.progress(i, total, os.path.basename(path))
                try:
                    if system == "ARCADE" and callable(make_zfb):
                        make_zfb(drive, "", path)
                    shutil.copy2(path, target)
                    copied += 1
                except Exception as exc:  # noqa: BLE001 - finish the batch
                    log.error("Could not copy %s: %s", path, exc)
                    failed.append(f"{os.path.basename(path)}: {exc}")
            ctx.progress(total, total, "done")
            return copied, failed

        def done(result: tuple[int, list[str]]) -> None:
            copied, failed = result
            if failed:
                self.notify(f"Copied {copied} of {len(files)} ROMs", "bad",
                            ("Details", lambda: self.tell("Add ROMs", "\n".join(failed[:12]))))
            else:
                self.notify(f"Copied {copied} ROMs into {system}", "good",
                            ("Add artwork", self.add_artwork))
            self.after_change()

        self.run_job(f"Copying {len(files)} ROMs", body, on_result=done)

    def delete_selection(self) -> None:
        self.delete_roms(self.selected())

    def delete_roms(self, roms: list[wc.RomEntry]) -> None:
        if not roms:
            return
        use_trash = self.config.get_bool("use_trash")
        if self.config.get_bool("confirm_delete"):
            names = "\n".join(f"  • {rom.name}" for rom in roms[:8])
            if len(roms) > 8:
                names += f"\n  …and {len(roms) - 8} more"
            verb = "Move to the Wolfpole bin" if use_trash else "Permanently delete"
            if not self.ask("Delete ROMs",
                            f"{verb}:\n\n{names}", "Delete", danger=not use_trash):
                return

        paths = [rom.path for rom in roms]
        trash = self.trash
        tf = wc.optional_import("tadpole_functions")
        legacy_delete = getattr(tf, "deleteROM", None) if tf else None

        def body(ctx: wc.JobContext) -> tuple[int, list[str]]:
            removed, failed = 0, []
            total = len(paths)
            for i, path in enumerate(paths):
                ctx.progress(i, total, os.path.basename(path))
                try:
                    if use_trash:
                        ok = trash.put(path)
                    elif callable(legacy_delete) and not path.lower().endswith(".zfb"):
                        ok = bool(legacy_delete(path))
                    else:
                        os.remove(path)
                        ok = True
                    removed += 1 if ok else 0
                    if not ok:
                        failed.append(os.path.basename(path))
                except OSError as exc:
                    failed.append(f"{os.path.basename(path)}: {exc}")
            ctx.progress(total, total, "done")
            return removed, failed

        def done(result: tuple[int, list[str]]) -> None:
            removed, failed = result
            self._undo_count = removed if use_trash else 0
            if failed:
                self.notify(f"Removed {removed} of {len(paths)}", "bad",
                            ("Details", lambda: self.tell("Delete", "\n".join(failed[:12]))))
            elif use_trash:
                self.notify(f"Moved {removed} ROM(s) to the bin", "good",
                            ("Undo", self.undo_delete))
            else:
                self.notify(f"Deleted {removed} ROM(s)", "good")
            self.after_change()

        self.run_job(f"Deleting {len(paths)} ROMs", body, on_result=done)

    def undo_delete(self) -> None:
        if not self._undo_count:
            self.notify("Nothing to undo")
            return
        entries = self.trash.last_batch(self._undo_count)
        restored = self.trash.restore(entries)
        self._undo_count = 0
        self.notify(f"Restored {restored} ROM(s)", "good")
        self.after_change()

    def empty_trash(self) -> None:
        size = self.trash.size()
        if not size:
            self.notify("The bin is already empty")
            return
        if self.ask("Empty the bin",
                    f"Permanently delete {wc.human_size(size)} of ROMs held in the "
                    "Wolfpole bin? This cannot be undone.", "Empty", danger=True):
            self.trash.empty()
            self._undo_count = 0
            self.notify("Bin emptied", "good")

    def rename_rom(self, rom: wc.RomEntry) -> None:
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename ROM", "New name (without extension):", text=rom.name)
        if ok and name.strip():
            self.apply_rename(rom, name.strip())

    def apply_rename(self, rom: wc.RomEntry, new_name: str) -> None:
        source = Path(rom.path)
        target = source.with_name(new_name + source.suffix)
        if target.exists():
            self.notify(f"{target.name} already exists here", "bad")
            return
        try:
            source.rename(target)
        except OSError as exc:
            self.notify("Rename failed", "bad",
                        ("Details", lambda e=str(exc): self.tell("Rename", e)))
            return
        rom.path, rom.name = str(target), new_name
        self.notify(f"Renamed to {new_name}", "good")
        self.after_change()

    def change_core(self) -> None:
        roms = self.selected()
        if not roms:
            self.notify("Select the ROMs you want to re-core first")
            return
        dialog_cls = wc.optional_import("dialogs.MulticoreChange", "MulticoreChange")
        if dialog_cls is None:
            self.notify("The multicore dialogs are not available here", "bad")
            return
        self.config.cDir = self.drive
        self.config.cCon = self.system
        self.config.gList = [rom.path for rom in roms]
        try:
            qexec(dialog_cls(self.config))
        except Exception as exc:  # noqa: BLE001
            self.tell("Change core", str(exc))
            return
        for rom in roms:
            rom.core_checked = False
        self.after_change()

    def rebuild_multicore(self, arcade_mode: bool) -> None:
        if not self._need_card():
            return
        mf = wc.optional_import("multicore_functions")
        fn = getattr(mf, "makeMulticoreROMList_ARCADEMode" if arcade_mode
                     else "makeMulticoreROMList", None)
        if not callable(fn):
            self.notify("multicore_functions is not available", "bad")
            return
        drive = self.drive

        def done(count: Any) -> None:
            self.notify(f"Found {int(count or 0)} ROMs in the multicore folders", "good")
            self.after_change()

        self.run_job("Rebuilding multicore list",
                     lambda ctx: fn(drive), on_result=done)

    # -- artwork -----------------------------------------------------------
    def add_artwork(self) -> None:
        if not self._need_card():
            return
        if self.system == "ARCADE":
            self.tell("Artwork",
                      "Arcade ROMs carry their art inside the .zfb stub, so there is "
                      "nothing to download. Use Replace artwork on a single ROM instead.")
            return
        if self.config.get_bool("download_thumbnails") and self.catalogues.online:
            self._download_artwork()
        else:
            self._import_artwork()

    def _import_artwork(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose a folder of images named after your ROMs")
        if not folder:
            return
        add = self._artwork_writer()
        if add is None:
            return
        drive, system = self.drive, self.system
        overwrite = self.config.get_bool("overwrite_thumbnails")
        roms = {rom.stem.lower(): rom.path for rom in self.model.roms()}

        def body(ctx: wc.JobContext) -> tuple[int, int]:
            images = [e for e in os.scandir(folder) if e.is_file()
                      and e.name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp"))]
            applied = failed = 0
            total = len(images)
            for i, image in enumerate(images):
                ctx.progress(i, total, image.name)
                rom_path = roms.get(os.path.splitext(image.name)[0].lower())
                if not rom_path:
                    continue
                try:
                    applied += 1 if add(rom_path, drive, system, image.path, overwrite) else 0
                except Exception as exc:  # noqa: BLE001
                    log.error("addThumbnail failed: %s", exc)
                    failed += 1
            ctx.progress(total, total, "done")
            return applied, failed

        self.run_job("Importing artwork", body,
                     on_result=lambda r: self._artwork_done(*r))

    def _download_artwork(self) -> None:
        add = self._artwork_writer()
        requests = wc.optional_import("requests")
        if add is None:
            return
        if requests is None:
            self._import_artwork()
            return
        drive, system = self.drive, self.system
        overwrite = self.config.get_bool("overwrite_thumbnails")
        roms = {rom.stem.lower(): rom.path for rom in self.model.roms()}
        catalogues = self.catalogues

        def body(ctx: wc.JobContext) -> tuple[int, int]:
            names = catalogues.fetch_thumbnail_index(ctx, system)
            wanted = [n for n in names if os.path.splitext(n)[0].lower() in roms]
            applied = failed = 0
            total = max(1, len(wanted))
            temp_dir = Path(catalogues.cache_dir) / "art"
            temp_dir.mkdir(parents=True, exist_ok=True)
            for i, name in enumerate(wanted):
                ctx.progress(i, total, f"{name}  ({i + 1} of {len(wanted)})")
                rom_path = roms[os.path.splitext(name)[0].lower()]
                local = temp_dir / name
                try:
                    response = requests.get(wc.Catalogues.art_url(system, name), timeout=30)
                    response.raise_for_status()
                    local.write_bytes(response.content)
                    applied += 1 if add(rom_path, drive, system, str(local), overwrite) else 0
                except Exception as exc:  # noqa: BLE001
                    log.warning("Artwork failed for %s: %s", name, exc)
                    failed += 1
                finally:
                    local.unlink(missing_ok=True)
            ctx.progress(total, total, "done")
            return applied, failed

        self.run_job(f"Downloading artwork for {system}", body,
                     on_result=lambda r: self._artwork_done(*r))

    def _artwork_writer(self) -> Callable[..., Any] | None:
        tf = wc.optional_import("tadpole_functions")
        add = getattr(tf, "addThumbnail", None) if tf else None
        if not callable(add):
            self.notify("tadpole_functions.addThumbnail is unavailable", "bad")
            return None
        return add

    def _artwork_done(self, applied: int, failed: int) -> None:
        self.cache.clear()
        tone = "bad" if failed and not applied else "good"
        self.notify(f"{applied} covers added" + (f", {failed} failed" if failed else ""), tone)
        self.after_change()

    def replace_artwork(self, rom: wc.RomEntry) -> None:
        add = self._artwork_writer()
        if add is None:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Choose artwork for {rom.name}", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        drive, system, rom_path = self.drive, self.system, rom.path

        def body(ctx: wc.JobContext) -> bool:
            ctx.status("converting…")
            return bool(add(rom_path, drive, system, path, True))

        def done(ok: bool) -> None:
            self.cache.clear()
            if ok:
                self.notify(f"Artwork updated for {rom.name}", "good")
                self.after_change()
            else:
                self.notify("That image could not be converted", "bad")

        self.run_job("Updating artwork", body, on_result=done, cancellable=False)

    def export_artwork(self, rom: wc.RomEntry) -> None:
        image = wc.decode_thumbnail(rom.path)
        if image is None:
            self.notify("This ROM has no embedded artwork")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save artwork", f"{rom.stem}.png", "PNG image (*.png)")
        if not path:
            return
        if image.save(path, "PNG"):
            self.notify(f"Written to {os.path.basename(path)}", "good")
        else:
            self.notify("Could not write that file", "bad")

    # -- rebuilds ----------------------------------------------------------
    def rebuild_current(self) -> None:
        if not self.has_card or wc.optional_import("frogtool") is None:
            self.reload_roms()
            return
        drive, system = self.drive, self.system
        self.run_job(f"Rebuilding {system}", wc.rebuild_system, drive, system,
                     on_result=lambda _: self.reload_roms(), quiet=True)

    def rebuild_everything(self) -> None:
        if not self._need_card():
            return
        if wc.optional_import("frogtool") is None:
            self.notify("frogtool is not available, so indexes cannot be rebuilt", "bad")
            return

        def done(results: dict[str, str]) -> None:
            bad = {k: v for k, v in results.items() if str(v).startswith("error:")}
            if bad:
                detail = "\n".join(f"{k}: {v}" for k, v in bad.items())
                self.notify(f"{len(results) - len(bad)} of {len(results)} systems rebuilt",
                            "bad", ("Details", lambda: self.tell("Rebuild", detail)))
            else:
                self.notify(f"Rebuilt all {len(results)} systems", "good")
            self.refresh_counts()
            self.reload_roms()

        self.run_job("Rebuilding every system", wc.rebuild_all, self.drive, on_result=done)

    # -- catalogues --------------------------------------------------------
    def _load_catalogues(self) -> None:
        self.run_job("Firmware list", self.catalogues.fetch_firmware,
                     on_result=self._firmware_loaded, quiet=True,
                     cancellable=False)
        self.run_job("Theme list", self.catalogues.fetch_themes, quiet=True,
                     cancellable=False,
                     on_result=lambda data: self._catalogue_loaded("themes", data))
        self.run_job("Music list", self.catalogues.fetch_music, quiet=True,
                     cancellable=False,
                     on_result=lambda data: self._catalogue_loaded("music", data))
        self.run_job("Boot logo list", self.catalogues.fetch_bootlogos, quiet=True,
                     cancellable=False,
                     on_result=lambda data: self._catalogue_loaded("bootlogos", data))

    def refresh_catalogues(self) -> None:
        for name in ("firmware", "themes", "music", "bootlogos"):
            try:
                (self.paths.cache_dir / f"{name}.json").unlink(missing_ok=True)
            except OSError:
                pass
        self._load_catalogues()
        self.notify("Refreshing the online lists…")

    def _firmware_titles(self) -> list[str]:
        titles = [item["title"] for item in self.firmware.get("official", [])]
        titles += [item["title"] for item in self.firmware.get("multicore", [])]
        return titles

    def _firmware_loaded(self, data: dict[str, Any]) -> None:
        self.firmware = data or {}
        titles = self._firmware_titles()
        self.card_firmware.set_choices(titles, "Offline")
        self.builder_page.set_firmware(titles)

    def _catalogue_loaded(self, kind: str, data: dict[str, str]) -> None:
        data = data or {}
        setattr(self, kind if kind != "bootlogos" else "bootlogos", data)
        card = {"themes": self.card_theme, "music": self.card_music,
                "bootlogos": self.card_logo}[kind]
        card.set_choices(sorted(data.keys()), "Offline")

    def _firmware_url(self, title: str) -> str:
        for group in ("official", "multicore"):
            for item in self.firmware.get(group, []):
                if item["title"] == title:
                    return item["link"]
        return ""

    def _install_selected_firmware(self) -> None:
        title = self.card_firmware.selected()
        if not title:
            self.notify("The firmware list has not loaded yet")
            return
        self.install_firmware(title, self._firmware_url(title))

    def install_firmware(self, title: str, url: str) -> None:
        if not self._need_card() or not url:
            return
        if not self.ask("Install firmware",
                        f"Install “{title}” onto {self.drive}?\n\nYour ROMs and saves "
                        "stay put, but the system files are replaced. Make sure the "
                        "card is not in use anywhere else.", "Install"):
            return
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "downloadAndExtractZIPBar", None) if tf else None
        if not callable(fn):
            self.notify("tadpole_functions is not available", "bad")
            return
        drive = self.drive

        def body(ctx: wc.JobContext, handle: TaskHandle) -> bool:
            ctx.status("downloading…")
            return bool(fn(drive, url, handle))

        def done(ok: bool) -> None:
            if ok:
                self.notify("Firmware installed — rebuilding indexes", "good")
                self.rebuild_everything()
            else:
                self.notify("The firmware could not be installed", "bad")

        self.run_job(f"Installing {title}", body, on_result=done,
                     cancellable=False, needs_handle=True)

    def detect_firmware(self) -> None:
        if not self._need_card():
            return
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "bisrv_getFirmwareVersion", None) if tf else None
        if not callable(fn):
            self.notify("tadpole_functions is not available", "bad")
            return
        bisrv = os.path.join(self.drive, "bios", "bisrv.asd")

        def done(version: str) -> None:
            if not version:
                self.tell("Firmware version",
                          "The build could not be identified. Another tool may have "
                          "patched bisrv.asd.")
                return
            newest = (self.firmware.get("multicore") or [{}])[-1].get("title", "")
            body = f"This card is running:\n\n{version}"
            if newest:
                body += f"\n\nNewest build Wolfpole knows about: {newest}"
            self.tell("Firmware version", body)

        self.run_job("Reading bisrv.asd", lambda ctx: str(fn(bisrv) or ""),
                     on_result=done, cancellable=False)

    # -- look and sound ----------------------------------------------------
    def apply_theme_pack(self, name: str, local: str = "") -> None:
        if not self._need_card():
            return
        url = self.themes.get(name, "") if name else ""
        if not url and not local:
            self.notify("Pick a theme, or use the local file card")
            return
        if not self.ask("Change theme",
                        "Applying a theme overwrites your game shortcut icons. You "
                        "can set them again afterwards.", "Apply"):
            return
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "changeTheme", None) if tf else None
        if not callable(fn):
            self.notify("tadpole_functions is not available", "bad")
            return
        drive = self.drive

        def body(ctx: wc.JobContext, handle: TaskHandle) -> Any:
            ctx.status("unpacking theme…")
            return fn(drive, url, local, handle.progress)

        self.run_job(f"Applying theme {name or Path(local).name}", body,
                     cancellable=False, needs_handle=True,
                     on_result=lambda ok: self.notify(
                         "Theme applied" if ok is not False else "Theme failed",
                         "good" if ok is not False else "bad"))

    def change_music(self, name: str, local: str = "") -> None:
        if not self._need_card():
            return
        source = local or self.music.get(name, "")
        if not source:
            self.notify("Pick a track, or use the local file card")
            return
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "changeBackgroundMusic", None) if tf else None
        if not callable(fn):
            self.notify("tadpole_functions is not available", "bad")
            return
        drive = self.drive
        kwargs = {"url": source} if source.startswith("http") else {"file": source}

        self.run_job(f"Setting music{f' — {name}' if name else ''}",
                     lambda ctx: bool(fn(drive, **kwargs)), cancellable=False,
                     on_result=lambda ok: self.notify(
                         "Background music updated" if ok else "Music change failed",
                         "good" if ok else "bad"))

    def change_boot_logo(self, name: str, local: str = "") -> None:
        if not self._need_card():
            return
        tf = wc.optional_import("tadpole_functions")
        change = getattr(tf, "changeBootLogo", None) if tf else None
        download = getattr(tf, "downloadFileFromGithub", None) if tf else None
        if not callable(change):
            self.notify("tadpole_functions is not available", "bad")
            return
        url = self.bootlogos.get(name, "") if name else ""
        if not url and not local:
            self.notify("Pick a logo, or use the local file card")
            return

        bisrv = os.path.join(self.drive, "bios", "bisrv.asd")
        cache_file = str(self.paths.cache_dir / "bootlogo.tmp")

        def body(ctx: wc.JobContext, handle: TaskHandle) -> bool:
            source = local
            if url:
                if not callable(download):
                    raise RuntimeError("downloadFileFromGithub is unavailable.")
                ctx.status(f"downloading {name}…")
                if not download(cache_file, url):
                    raise RuntimeError("The boot logo could not be downloaded.")
                source = cache_file
            ctx.status("patching bisrv.asd…")
            try:
                return bool(change(bisrv, source, handle))
            finally:
                if url:
                    Path(cache_file).unlink(missing_ok=True)

        def done(ok: bool) -> None:
            if ok:
                self.notify("Boot logo updated", "good")
            else:
                self.tell("Boot logo",
                          "The logo could not be written. Wolfpole only understands "
                          "stock firmware files — if another tool has patched "
                          "bisrv.asd, reinstall the firmware first.")

        self.run_job("Updating boot logo", body, on_result=done,
                     cancellable=False, needs_handle=True)

    def _local_asset(self) -> None:
        """One entry point for 'use my own file' across theme, music and logo."""
        kinds = ["Theme ZIP", "Background music", "Boot logo image"]
        kind, ok = QtWidgets.QInputDialog.getItem(
            self, "Local file", "What are you installing?", kinds, 0, False)
        if not ok:
            return
        if kind == kinds[0]:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Choose a theme ZIP", "", "Theme archive (*.zip)")
            if path:
                self.apply_theme_pack("", path)
        elif kind == kinds[1]:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Choose audio", "", "Audio (*.wav *.mp3 *.ogg)")
            if path:
                self.change_music("", path)
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Choose an image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
            if path:
                self.change_boot_logo("", path)

    def strip_shortcut_text(self) -> None:
        if not self._need_card():
            return
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "stripShortcutText", None) if tf else None
        if not callable(fn):
            self.notify("tadpole_functions is not available", "bad")
            return
        drive = self.drive
        self.run_job("Removing shortcut text", lambda ctx: fn(drive), cancellable=False,
                     on_result=lambda _: self.notify("Shortcut captions removed", "good"))

    # -- patches -----------------------------------------------------------
    def gba_bios_fix(self) -> None:
        if not self._need_card():
            return
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "GBABIOSFix", None) if tf else None
        if not callable(fn):
            self.notify("tadpole_functions is not available", "bad")
            return
        drive = self.drive

        def body(ctx: wc.JobContext) -> Any:
            ctx.status("copying gba_bios.bin…")
            try:
                return fn(drive)
            except Exception as exc:  # noqa: BLE001 - plain English, please
                raise RuntimeError(
                    "The GBA BIOS could not be installed. Check that gba_bios.bin is "
                    f"in the card's bios folder.\n\nDetail: {exc}") from exc

        self.run_job("Installing GBA BIOS", body, cancellable=False,
                     on_result=lambda _: self.notify("GBA BIOS installed", "good"))

    def battery_patch(self) -> None:
        if not self._need_card():
            return
        tf = wc.optional_import("tadpole_functions")
        get_version = getattr(tf, "bisrv_getFirmwareVersion", None) if tf else None
        patcher_cls = getattr(tf, "BatteryPatcher", None) if tf else None
        if not callable(get_version) or patcher_cls is None:
            self.notify("This build of tadpole_functions cannot patch batteries", "bad")
            return
        if not self.ask("Battery patch",
                        "Patch bisrv.asd with the community battery improvements? "
                        "Only stock v1.6 and v1.71 firmware are supported.", "Patch"):
            return
        bisrv = os.path.join(self.drive, "bios", "bisrv.asd")

        def body(ctx: wc.JobContext, handle: TaskHandle) -> str:
            ctx.status("checking firmware…")
            patcher = patcher_cls(bisrv, get_version(bisrv))
            if patcher.check_patch_applied():
                return "already"
            ctx.status("patching…")
            return "ok" if patcher.patch_firmware(handle.progress) else "failed"

        def done(outcome: str) -> None:
            if outcome == "already":
                self.notify("This card already has the battery patch")
            elif outcome == "ok":
                self.notify("Battery improvements applied", "good")
            else:
                self.tell("Battery patch",
                          "The patch did not apply. This usually means the firmware "
                          "is not a stock v1.6 or v1.71 build.")

        self.run_job("Patching firmware", body, on_result=done,
                     cancellable=False, needs_handle=True)

    def bootloader_patch(self) -> None:
        """Stage the community bootloader fix, checksum-verified.

        The original recursed on a checksum mismatch with no bound. This retries
        three times and then stops with an explanation.
        """
        if not self._need_card():
            return
        if not self.ask("Bootloader patch",
                        "This copies the community fix onto the card; the console "
                        "applies it on the next boot. Make sure it is charged.",
                        "Download"):
            return
        tf = wc.optional_import("tadpole_functions")
        download = getattr(tf, "downloadFileFromGithub", None) if tf else None
        if not callable(download):
            self.notify("tadpole_functions is not available", "bad")
            return

        folder = Path(self.drive) / "UpdateFirmware"
        target = folder / "Firmware.upk"

        def body(ctx: wc.JobContext) -> str:
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            for attempt in range(1, 4):
                ctx.progress(attempt, 3, f"downloading (attempt {attempt} of 3)…")
                if not download(str(target), BOOTLOADER_URL):
                    continue
                if wc.sha256_file(target) == BOOTLOADER_SHA256:
                    ctx.progress(3, 3, "verified")
                    return "ok"
                log.warning("Bootloader checksum mismatch on attempt %s", attempt)
                target.unlink(missing_ok=True)
            return "bad"

        def done(outcome: str) -> None:
            if outcome != "ok":
                shutil.rmtree(folder, ignore_errors=True)
                self.tell("Bootloader patch",
                          "The patch could not be downloaded, or failed verification "
                          "three times.\n\nCheck your connection, then see "
                          f"{BOOTLOADER_DOC_URL} or ask on Discord.")
                return
            self.tell("Patch staged — now use the console",
                      "1. Eject the card from this computer\n"
                      "2. Put it in the SF2000 and switch on\n"
                      "3. A message appears bottom-left while it patches\n"
                      "4. Wait for the main menu, power off, bring the card back")
            if self.ask("Bootloader patch", "Did the console show the message and boot?",
                        "Yes, it worked"):
                self.notify("Bootloader patched — this card is safer to edit now", "good")
            else:
                self.tell("Bootloader patch",
                          f"Sorry. See {BOOTLOADER_DOC_URL}, or ask on {DISCORD_URL}.")
            shutil.rmtree(folder, ignore_errors=True)

        self.run_job("Bootloader patch", body, on_result=done, cancellable=False)

    def build_fresh_card(self) -> None:
        """The old menu entry now opens the builder page."""
        self.show_page("build")

    def fix_boot_light(self) -> None:
        if not self._need_card():
            return
        if not self.ask("Light boot fix",
                        "This writes and deletes a scratch file in the card's bios "
                        "folder, which shuffles the file allocation table and often "
                        "clears the bootloader bug. Nothing is erased.", "Run"):
            return
        temp = Path(self.drive) / "bios" / "wolfpole.tmp"
        try:
            temp.touch()
            temp.unlink()
        except OSError as exc:
            self.tell("Light boot fix", f"Could not write to the bios folder:\n\n{exc}")
            return
        if self.ask("Light boot fix",
                    "Eject the card, put it in the console and switch on.\n\nDid it boot?",
                    "Yes, it booted"):
            if self.ask("Bootloader patch",
                        "Apply the bootloader patch so this stops happening?", "Patch now"):
                self.bootloader_patch()
        else:
            self.tell("Light boot fix",
                      "Try the full rebuild next — but back up your saves first, "
                      "from Device ▸ Back up saves.")

    def fix_boot_full(self) -> None:
        self.tell("Full rebuild",
                  "A full rebuild reformats the card and reinstalls the firmware, "
                  "which erases every ROM and save on it.\n\n"
                  "1. Back up your saves (Device ▸ Back up saves)\n"
                  "2. Format the card to FAT32 with your OS disk tools\n"
                  "3. Device ▸ Build a fresh card\n"
                  "4. Apply the bootloader patch when it boots again")

    # -- data --------------------------------------------------------------
    def backup_saves(self) -> None:
        if not self._need_card():
            return
        tf = wc.optional_import("tadpole_functions")
        fn = getattr(tf, "createSaveBackup", None) if tf else None
        if not callable(fn):
            self.notify("tadpole_functions is not available", "bad")
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Where should the backup go?", str(Path.home()))
        if not folder:
            return
        if Path(folder).resolve() == Path(self.drive).resolve():
            self.tell("Back up saves",
                      "Saving the backup onto the card it came from defeats the "
                      "purpose. Choose a folder on your computer.")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = str(Path(folder) / f"SF2000SaveBackup_{stamp}.zip")
        drive = self.drive

        def done(ok: bool) -> None:
            if ok:
                self.notify("Saves backed up", "good",
                            ("Show", lambda: wc.open_in_file_manager(target)))
            else:
                self.notify("The backup could not be created", "bad")

        self.run_job("Backing up saves", lambda ctx: bool(fn(drive, target)),
                     on_result=done, cancellable=False)

    def copy_library_to_card(self) -> None:
        source = self.config.local_library()
        if not source or not Path(source).is_dir():
            self.tell("Copy library",
                      "Set a local library folder in Preferences first. That folder "
                      "is a mirror of your card that Wolfpole can copy onto real "
                      "hardware.")
            return
        targets = [d for d in self.drives if not d.local]
        if not targets:
            self.notify("No SF2000 card is connected", "bad")
            return
        labels = [d.describe() for d in targets]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self, "Copy library", "Copy onto which card?", labels, 0, False)
        if not ok:
            return
        destination = targets[labels.index(choice)].mountpoint
        if not self.ask("Copy library",
                        f"Copy everything from\n{source}\nonto\n{destination}?\n\n"
                        "Files with the same name are overwritten, saves included.",
                        "Copy", danger=True):
            return

        def body(ctx: wc.JobContext) -> tuple[int, int]:
            files = [p for p in Path(source).rglob("*") if p.is_file()]
            total = len(files)
            copied = skipped = 0
            for i, path in enumerate(files):
                ctx.progress(i, total, f"{path.name}  ({i + 1} of {total})")
                target = Path(destination) / path.relative_to(source)
                if target.exists():
                    existing, current = target.stat(), path.stat()
                    if (existing.st_size == current.st_size
                            and int(existing.st_mtime) >= int(current.st_mtime)):
                        skipped += 1
                        continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied += 1
            ctx.progress(total, total, "done")
            return copied, skipped

        def done(result: tuple[int, int]) -> None:
            copied, skipped = result
            self.notify(f"Copied {copied} files, {skipped} already up to date", "good")
            self.after_change(rebuild=False)

        self.run_job("Copying library to card", body, on_result=done)

    def export_csv(self) -> None:
        roms = self.model.roms()
        if not roms:
            self.notify("There is nothing to export")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export ROM list", f"{self.system}-roms.csv", "CSV file (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Name", "File", "Bytes", "Size", "Format", "Core",
                                 "Shortcut", "Path"])
                for rom in roms:
                    writer.writerow([rom.name, rom.filename, rom.size,
                                     wc.human_size(rom.size), rom.ext.lstrip("."),
                                     rom.core, rom.slot or "", rom.path])
        except OSError as exc:
            self.tell("Export", f"Could not write the file:\n\n{exc}")
            return
        self.notify(f"Exported {len(roms)} rows", "good",
                    ("Show", lambda: wc.open_in_file_manager(path)))

    # -- app ---------------------------------------------------------------
    def show_palette(self) -> None:
        qexec(CommandPalette(self.commands, self))

    def show_preferences(self) -> None:
        sheet = PreferencesSheet(self.config, self)
        if qexec(sheet):
            sheet.apply()
            app = QtWidgets.QApplication.instance()
            if app is not None:
                self.palette_colours = apply_shell_theme(app, self.config.get("theme"))
                self.library.delegate.palette = self.palette_colours
                self.sidebar.chip.ring.set_palette_colours(self.palette_colours)
            self.watcher.poke()
            self.notify("Preferences saved", "good")
            self.reload_roms()

    def _need_card(self) -> bool:
        if self.has_card:
            return True
        self.notify("No card selected — insert one, or set a local library", "bad",
                    ("Preferences", self.show_preferences))
        return False

    # -- filters, scope, bulk tools ---------------------------------------
    def on_filter_changed(self, key: str) -> None:
        self.proxy.set_mode(key)
        self.config.set("filter_mode", key)
        self._update_subtitle()
        self.library.show_content(self.proxy.rowCount() > 0)
        if self.proxy.rowCount() == 0 and self.model.rowCount():
            self.library.empty.set(
                "Nothing matches this filter",
                "No ROM in this view fits the filter you picked.",
                ("Show everything", lambda: (self.library.set_filter("all"),
                                             self.on_filter_changed("all"))))

    def on_scope_changed(self, everything: bool) -> None:
        self.scope_all = everything
        self.config.set("scope_all", int(everything))
        self.library.search.setPlaceholderText(
            "Search every console…" if everything else "Search this system…")
        self.reload_roms()

    def show_tools_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        selection = len(self.selected())
        menu.addAction("Tidy up ROM names…", self.tidy_names)
        move = menu.addAction(
            f"Move {selection} ROM(s) to another system…" if selection
            else "Move selection to another system…", self.move_to_system)
        move.setEnabled(bool(selection) and not self.scope_all)
        menu.addAction("Find duplicate ROMs", self.find_duplicates)
        menu.addSeparator()
        menu.addAction("Export shortcut layout…", self.export_shortcuts)
        menu.addAction("Import shortcut layout…", self.import_shortcuts)
        menu.addSeparator()
        menu.addAction("Export this list to CSV…", self.export_csv)
        menu.addAction("Open this folder", self.open_current_folder)
        popup = getattr(menu, "exec", None) or menu.exec_
        popup(self.library.tools_button.mapToGlobal(
            QtCore.QPoint(0, self.library.tools_button.height() + 4)))

    def open_current_folder(self) -> None:
        if not self._need_card():
            return
        target = Path(self.drive) if self.scope_all else Path(self.drive) / self.system
        wc.open_in_file_manager(target)

    def rename_selection(self) -> None:
        roms = self.selected()
        if len(roms) == 1:
            self.rename_rom(roms[0])
        elif roms:
            self.tidy_names()

    def copy_paths(self) -> None:
        roms = self.selected()
        if not roms:
            return
        QtWidgets.QApplication.clipboard().setText(
            "\n".join(rom.path for rom in roms))
        self.notify(f"Copied {len(roms)} path(s) to the clipboard")

    def tidy_names(self) -> None:
        """Strip region tags and underscores from filenames, in bulk."""
        roms = self.selected() or self.model.roms()
        if not roms:
            return
        proposals = []
        for rom in roms:
            tidy = wc.tidy_title(rom.filename)
            if tidy and tidy != rom.stem:
                target = Path(rom.path).with_name(tidy + Path(rom.path).suffix)
                if not target.exists():
                    proposals.append((rom, tidy))
        if not proposals:
            self.notify("Those names are already tidy")
            return

        preview = "\n".join(f"{rom.stem}   →   {tidy}" for rom, tidy in proposals[:14])
        if len(proposals) > 14:
            preview += f"\n…and {len(proposals) - 14} more"
        sheet = Sheet("Tidy up ROM names",
                      f"{len(proposals)} of {len(roms)} files would be renamed on the "
                      "card. The SF2000 shows these names, so this is what you will "
                      "see on the console.", self, confirm="Rename")
        view = QtWidgets.QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(preview)
        view.setMinimumHeight(240)
        sheet.extra.addWidget(view)
        sheet.setMinimumWidth(620)
        if not qexec(sheet):
            return

        pairs = [(rom.path, str(Path(rom.path).with_name(tidy + Path(rom.path).suffix)))
                 for rom, tidy in proposals]

        def body(ctx: wc.JobContext) -> tuple[int, list[str]]:
            done, failed = 0, []
            for index, (source, target) in enumerate(pairs):
                ctx.progress(index, len(pairs), os.path.basename(target))
                try:
                    os.rename(source, target)
                    done += 1
                except OSError as exc:
                    failed.append(f"{os.path.basename(source)}: {exc}")
            return done, failed

        def finished(result: tuple[int, list[str]]) -> None:
            done, failed = result
            if failed:
                self.notify(f"Renamed {done}, {len(failed)} failed", "bad",
                            ("Details", lambda: self.tell("Tidy names",
                                                          "\n".join(failed[:12]))))
            else:
                self.notify(f"Renamed {done} ROM(s)", "good")
            self.after_change()

        self.run_job(f"Renaming {len(pairs)} ROMs", body, on_result=finished)

    def move_to_system(self) -> None:
        roms = self.selected()
        if not roms or not self._need_card():
            return
        options = [name for name in wc.known_systems() if name != self.system]
        if not options:
            return
        labels = [f"{name} — {wc.system_label(name)}" for name in options]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self, "Move ROMs", "Move the selection into which system?", labels, 0, False)
        if not ok:
            return
        target_system = options[labels.index(choice)]
        source_system = self.system
        drive = self.drive
        paths = [rom.path for rom in roms]

        def body(ctx: wc.JobContext) -> tuple[int, list[str]]:
            folder = Path(drive) / target_system
            folder.mkdir(parents=True, exist_ok=True)
            moved, failed = 0, []
            for index, path in enumerate(paths):
                ctx.progress(index, len(paths), os.path.basename(path))
                destination = folder / os.path.basename(path)
                if destination.exists():
                    failed.append(f"{destination.name}: already in {target_system}")
                    continue
                try:
                    shutil.move(path, str(destination))
                    moved += 1
                except (OSError, shutil.Error) as exc:
                    failed.append(f"{os.path.basename(path)}: {exc}")
            # Both folders' indexes are now stale.
            for system in (source_system, target_system):
                try:
                    wc.rebuild_system(ctx, drive, system)
                except Exception as exc:  # noqa: BLE001 - frogtool may be absent
                    log.debug("Rebuild after move failed for %s: %s", system, exc)
            return moved, failed

        def finished(result: tuple[int, list[str]]) -> None:
            moved, failed = result
            if failed:
                self.notify(f"Moved {moved}, {len(failed)} skipped", "bad",
                            ("Details", lambda: self.tell("Move ROMs",
                                                          "\n".join(failed[:12]))))
            else:
                self.notify(f"Moved {moved} ROM(s) to {target_system}", "good")
            self.refresh_counts()
            self.reload_roms()

        self.run_job(f"Moving {len(paths)} ROMs", body, on_result=finished)

    def find_duplicates(self) -> None:
        if not self._need_card():
            return
        drive = self.drive

        def body(ctx: wc.JobContext) -> list[list[wc.RomEntry]]:
            return wc.find_duplicates(wc.scan_all_systems(ctx, drive))

        def finished(groups: list[list[wc.RomEntry]]) -> None:
            if not groups:
                self.notify("No duplicates found", "good")
                return
            wasted = sum(sum(rom.size for rom in group[1:]) for group in groups)
            lines = []
            for group in groups[:40]:
                where = ", ".join(f"{rom.system}/{rom.filename}" for rom in group)
                lines.append(f"{group[0].name}  ({len(group)} copies)\n    {where}")
            sheet = Sheet("Duplicate ROMs",
                          f"{len(groups)} title(s) appear more than once, using about "
                          f"{wc.human_size(wasted)} of extra space. Nothing has been "
                          "deleted — review the list and remove what you do not want.",
                          self)
            view = QtWidgets.QPlainTextEdit()
            view.setReadOnly(True)
            view.setPlainText("\n\n".join(lines))
            view.setMinimumHeight(320)
            sheet.extra.addWidget(view)
            sheet.setMinimumWidth(680)
            qexec(sheet)

        self.run_job("Looking for duplicates", body, on_result=finished)

    def export_shortcuts(self) -> None:
        if not self._need_card() or self.shortcuts is None:
            return
        layout = {system: {slot: self.shortcuts.filename_for(system, slot)
                           for slot in range(1, 5)}
                  for system in wc.ShortcutStore.SECTIONS}
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export shortcut layout", "wolfpole-shortcuts.json",
            "Layout (*.json)")
        if not path:
            return
        try:
            with wc.atomic_open(Path(path)) as handle:
                json.dump({"version": 1, "shortcuts": layout}, handle, indent=1)
        except OSError as exc:
            self.tell("Export shortcuts", str(exc))
            return
        self.notify("Shortcut layout exported", "good",
                    ("Show", lambda: wc.open_in_file_manager(path)))

    def import_shortcuts(self) -> None:
        if not self._need_card() or self.shortcuts is None:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import shortcut layout", "", "Layout (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            layout = payload["shortcuts"]
        except (OSError, ValueError, KeyError) as exc:
            self.tell("Import shortcuts", f"That file could not be read:\n\n{exc}")
            return
        applied = 0
        for system, slots in layout.items():
            for slot, filename in slots.items():
                if not filename:
                    continue
                try:
                    self.shortcuts.set_slot(system, int(slot), filename)
                    applied += 1
                except (TypeError, ValueError):
                    continue
        self.notify(f"Applied {applied} shortcut assignment(s)", "good")
        self.reload_roms()

    # -- artwork drop, watching, warnings ----------------------------------
    def apply_dropped_artwork(self, rom: wc.RomEntry, image_path: str) -> None:
        add = self._artwork_writer()
        if add is None:
            return
        drive, system, rom_path = self.drive, self.system, rom.path

        def body(ctx: wc.JobContext) -> bool:
            ctx.status("converting…")
            return bool(add(rom_path, drive, rom.system or system, image_path, True))

        def finished(ok: bool) -> None:
            self.cache.clear()
            if ok:
                self.notify(f"Artwork set for {rom.name}", "good")
                self.after_change()
            else:
                self.notify("That image could not be converted", "bad")

        self.run_job(f"Artwork for {rom.name}", body, on_result=finished,
                     cancellable=False)

    def _watch_current_folder(self) -> None:
        try:
            existing = self._watcher.directories()
            if existing:
                self._watcher.removePaths(existing)
            if self.has_card:
                folder = str(Path(self.drive) / self.system)
                if Path(folder).is_dir():
                    self._watcher.addPath(folder)
        except Exception as exc:  # noqa: BLE001 - watching is a convenience
            log.debug("Could not watch the folder: %s", exc)

    def _folder_changed(self) -> None:
        """Something changed on the card outside Wolfpole."""
        if self.sidebar.tray.busy or not self.has_card:
            return
        log.debug("Folder changed on disk; reloading")
        self.reload_roms()

    def _warn_if_full(self) -> None:
        free, total = wc.free_space(self.drive) if self.has_card else (0, 0)
        if total and free / total < 0.04:
            self.notify(
                f"Only {wc.human_size(free)} left on this card — saves can fail to "
                "write when it is this full", "bad")

    # -- overview ----------------------------------------------------------
    def refresh_overview(self) -> None:
        if not self.has_card:
            return
        drive = self.drive

        def body(ctx: wc.JobContext) -> dict[str, Any]:
            roms = wc.scan_all_systems(ctx, drive)
            counts: dict[str, int] = {}
            sizes: dict[str, int] = {}
            missing = 0
            for rom in roms:
                counts[rom.system] = counts.get(rom.system, 0) + 1
                sizes[rom.system] = sizes.get(rom.system, 0) + rom.size
                if not rom.has_thumbnail:
                    missing += 1
            free, total = wc.free_space(drive)
            return {"counts": counts, "sizes": sizes, "missing": missing,
                    "free": free, "total": total}

        def finished(data: dict[str, Any]) -> None:
            self._counts = data["counts"]
            self._sizes = data["sizes"]
            self.overview_page.set_summary(
                self.drive, data["counts"], data["sizes"], data["missing"],
                data["free"], data["total"])
            self.sidebar.set_counts(data["counts"])

        self.run_job("Summarising the card", body, on_result=finished, quiet=True)

    def on_overview_action(self, key: str) -> None:
        handlers = {
            "doctor": self.run_doctor,
            "health": self.run_health_check,
            "repair": self.repair_structure,
            "rebuild": self.rebuild_everything,
            "backup": self.backup_saves,
            "open": lambda: wc.open_in_file_manager(self.drive),
            "eject": self.safe_eject,
        }
        handler = handlers.get(key)
        if handler:
            handler()

    def run_doctor(self) -> None:
        if not self._need_card():
            return
        drive = self.drive

        def finished(report: Any) -> None:
            self.overview_page.checks.show_report(report)
            tone = "bad" if report.fatal else ("info" if report.warnings else "good")
            self.notify(f"Card check: {report.summary()}", tone,
                        ("Details", lambda: report_sheet(
                            self, "Card check", report, self.palette_colours)))

        self.run_job("Checking the card", wb.doctor, drive, on_result=finished)

    def run_health_check(self) -> None:
        target = self.drive
        if self.pages.currentWidget() is self.builder_page:
            volume = self.builder_page.selected_volume()
            if volume:
                target = volume["mountpoint"]
        if not target or target == NO_DRIVE:
            self.notify("Select a card first", "bad")
            return
        if not self.ask("Deep card test",
                        f"Write and read back about 48 MB of test data across "
                        f"{target}?\n\nThis is how counterfeit and failing cards are "
                        "caught. Existing files are not touched, and the test data is "
                        "removed afterwards.", "Run test"):
            return

        def finished(report: Any) -> None:
            if self.pages.currentWidget() is self.builder_page:
                self.builder_page.checks.show_report(report)
                self.builder_page.append(f"Deep test: {report.summary()}")
            else:
                self.overview_page.checks.show_report(report)
            tone = "bad" if report.fatal else "good"
            self.notify(f"Card test: {report.summary()}", tone,
                        ("Details", lambda: report_sheet(
                            self, "Deep card test", report, self.palette_colours)))

        self.run_job("Testing the card", wb.health_check, target, on_result=finished)

    def repair_structure(self) -> None:
        if not self._need_card():
            return
        created = wb.ensure_structure(Path(self.drive))
        if created:
            self.notify("Created: " + ", ".join(created), "good")
        else:
            self.notify("Every expected folder is already there", "good")
        self.refresh_counts()

    def safe_eject(self) -> None:
        if not self._need_card():
            return
        if self.sidebar.tray.busy and not self.ask(
                "Eject", "Work is still running. Eject anyway?", "Eject", danger=True):
            return
        target = self.drive

        def finished(result: tuple[bool, str]) -> None:
            ok, message = result
            self.notify(message, "good" if ok else "bad")

        self.run_job("Ejecting", lambda ctx: wb.safe_eject(target),
                     on_result=finished, cancellable=False)

    # -- builder page ------------------------------------------------------
    def refresh_build_targets(self) -> None:
        include = self.builder_page.show_all.isChecked()

        def finished(volumes: list[dict[str, Any]]) -> None:
            self.builder_page.set_volumes(volumes)
            self.builder_page.append(f"Found {len(volumes)} candidate volume(s).")

        self.run_job("Looking for volumes",
                     lambda ctx: wb.candidate_volumes(include),
                     on_result=finished, quiet=True, cancellable=False)

    def run_preflight(self) -> None:
        volume = self.builder_page.selected_volume()
        if volume is None:
            self.notify("Pick a target volume first", "bad")
            return
        mountpoint = volume["mountpoint"]
        require_empty = not self.builder_page.show_all.isChecked()
        self.builder_page.append(f"Running pre-flight checks on {mountpoint}…")

        def finished(report: Any) -> None:
            self.builder_page.checks.show_report(report)
            self.builder_page.append(f"Pre-flight: {report.summary()}")
            self.notify(f"Pre-flight: {report.summary()}",
                        "bad" if report.fatal else "good")

        self.run_job("Pre-flight checks", wb.preflight, mountpoint, 0, require_empty,
                     on_result=finished)

    def start_build(self) -> None:
        volume = self.builder_page.selected_volume()
        if volume is None:
            self.notify("Pick a target volume first", "bad")
            return
        title = self.builder_page.firmware.currentText()
        url = self._firmware_url(title)
        if not url:
            self.notify("The firmware list has not loaded yet", "bad")
            return

        mountpoint = volume["mountpoint"]
        warning = ""
        if not volume["empty"]:
            warning = ("\n\nThis volume is NOT empty. Matching files will be "
                       "overwritten.")
        if volume["removable"] is False:
            warning += ("\n\nThe system reports this as a fixed disk, not removable "
                        "media. Check the mountpoint very carefully.")
        if not self.ask(
                "Build the card",
                f"Write “{title}” to {mountpoint} "
                f"({wc.human_size(volume['total'])}, {volume['fstype'] or 'unknown fs'})?"
                + warning,
                "Build", danger=not volume["empty"]):
            return

        options = wb.BuildOptions(
            mountpoint=mountpoint,
            url=url,
            title=title,
            verify_after_write=self.builder_page.opt_verify.isChecked(),
            create_structure=self.builder_page.opt_structure.isChecked(),
            require_empty=not self.builder_page.show_all.isChecked(),
            restore_saves_from=self.builder_page.saves.text().strip(),
            resume=self.builder_page.opt_resume.isChecked(),
        )
        cache_dir = self.paths.cache_dir
        self.builder_page.set_busy(True)
        self.builder_page.append(f"Building {mountpoint} with {title}…")

        def progress(value: int, total: int, text: str) -> None:
            if text:
                self.builder_page.append(text)

        def finished(result: Any) -> None:
            self.builder_page.set_busy(False)
            if result.preflight is not None:
                self.builder_page.checks.show_report(result.preflight)
            if result.ok:
                self.builder_page.append("BUILD COMPLETE — " + result.message)
                self.notify("Card built and verified", "good",
                            ("Eject", lambda: self._eject_specific(mountpoint)))
                self._offer_bootloader(mountpoint)
            else:
                self.builder_page.append(
                    f"BUILD STOPPED at {result.stage}: {result.message}")
                for problem in result.problems[:20]:
                    self.builder_page.append(f"    {problem}")
                self.notify(f"Build stopped: {result.message}", "bad",
                            ("Details", lambda: self.tell(
                                f"Build stopped at {result.stage}",
                                result.message + (
                                    "\n\n" + "\n".join(result.problems[:15])
                                    if result.problems else ""))))

        job = self.run_job(f"Building {mountpoint}", wb.build_card, options, cache_dir,
                           on_result=finished)
        job.signals.progress.connect(
            lambda v, t, text: progress(v, t, text) if t == 0 else None, wc.QUEUED)
        job.signals.cancelled.connect(
            lambda: self.builder_page.set_busy(False), wc.QUEUED)
        job.signals.failed.connect(
            lambda message: (self.builder_page.set_busy(False),
                             self.builder_page.append("FAILED: " + message)), wc.QUEUED)

    def _eject_specific(self, mountpoint: str) -> None:
        def finished(result: tuple[bool, str]) -> None:
            ok, message = result
            self.notify(message, "good" if ok else "bad")

        self.run_job("Ejecting", lambda ctx: wb.safe_eject(mountpoint),
                     on_result=finished, cancellable=False)

    def _offer_bootloader(self, mountpoint: str) -> None:
        if not self.ask(
                "Bootloader patch",
                "The card is written and verified.\n\nPut it in the SF2000 and check "
                "it boots. Once it does, the bootloader patch stops the card "
                "corruption bug for good — it is needed only once per console.\n\n"
                "Stage the patch onto this card now?", "Stage the patch"):
            return
        previous = getattr(self, "_drive", "")
        self._drive = mountpoint
        try:
            self.bootloader_patch()
        finally:
            if previous:
                self._drive = previous

    # -- events ------------------------------------------------------------
    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "toasts"):
            self.toasts.reposition()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if not self.has_card or not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in wc.ROM_EXTENSIONS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        files = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.isLocalFile()
                 and Path(url.toLocalFile()).suffix.lower() in wc.ROM_EXTENSIONS]
        if files:
            event.acceptProposedAction()
            self.copy_roms_in(files)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.config.save_geometry(self)
        try:
            self.watcher.stop()
            self.watcher.wait(1500)
        except Exception:  # noqa: BLE001
            pass
        self.runner.wait(2500)
        log.info("Wolfpole closing")
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


STATUS_GLYPH = {"pass": "✓", "warn": "!", "fail": "✕"}


class CheckRow(QtWidgets.QFrame):
    """One line of a preflight, health or doctor report."""

    def __init__(self, check: Any, palette: dict[str, str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskRow")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(11)

        colour = {"pass": palette["ok"], "warn": palette["accent"],
                  "fail": palette["danger"]}.get(check.status, palette["dim"])
        mark = QtWidgets.QLabel(STATUS_GLYPH.get(check.status, "·"))
        mark.setFixedWidth(16)
        mark.setAlignment(ALIGN_CENTER)
        mark.setStyleSheet(f"color: {colour}; font-weight: 800; font-size: 14px;")
        layout.addWidget(mark)

        text = QtWidgets.QVBoxLayout()
        text.setSpacing(2)
        title = label(check.title, "taskTitle")
        text.addWidget(title)
        if check.detail:
            detail = label(check.detail, "taskDetail", wrap=True)
            text.addWidget(detail)
        layout.addLayout(text, 1)


class CheckList(QtWidgets.QWidget):
    """Scrollable list of report rows."""

    def __init__(self, palette: dict[str, str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colours = palette
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.placeholder = label("No checks have run yet.", "toolBody", wrap=True)
        self._layout.addWidget(self.placeholder)

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def show_report(self, report: Any) -> None:
        self.clear()
        for check in report.checks:
            self._layout.addWidget(CheckRow(check, self.palette_colours, self))
        self._layout.addStretch(1)

    def show_message(self, text: str) -> None:
        self.clear()
        self._layout.addWidget(label(text, "toolBody", wrap=True))
        self._layout.addStretch(1)


def report_sheet(parent: QtWidgets.QWidget, title: str, report: Any,
                 palette: dict[str, str]) -> None:
    """Show a Report in a sheet."""
    sheet = Sheet(title, report.summary(), parent)
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(qenum(QtWidgets.QFrame, "Shape", "NoFrame"))
    scroll.setMinimumHeight(320)
    holder = CheckList(palette)
    holder.show_report(report)
    scroll.setWidget(holder)
    sheet.extra.addWidget(scroll)
    sheet.setMinimumWidth(620)
    qexec(sheet)


# ---------------------------------------------------------------------------
# Overview page
# ---------------------------------------------------------------------------


class StatTile(QtWidgets.QFrame):
    def __init__(self, caption: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toolCard")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(2)
        self.value = label("—", "pageTitle")
        layout.addWidget(self.value)
        layout.addWidget(label(caption, "toolBody"))

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class SystemBar(QtWidgets.QFrame):
    """One console's share of the card, as a labelled bar."""

    clicked = Signal(str)

    def __init__(self, system: str, palette: dict[str, str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.system = system
        self.setCursor(POINTING_HAND)
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(4)

        self.name = label(wc.system_label(system), "toolBody")
        self.name.setFixedWidth(190)
        layout.addWidget(self.name, 0, 0)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("miniBar")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar, 0, 1)

        self.count = label("0", "taskDetail")
        self.count.setFixedWidth(120)
        self.count.setAlignment(ALIGN_RIGHT | ALIGN_VCENTER)
        layout.addWidget(self.count, 0, 2)

    def set_values(self, count: int, size: int, largest: int) -> None:
        self.bar.setValue(int(100 * size / largest) if largest else 0)
        self.count.setText(f"{count} · {wc.human_size(size)}")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.clicked.emit(self.system)
        super().mousePressEvent(event)


class OverviewPage(QtWidgets.QWidget):
    """Dashboard for the selected card: what is on it, and is it healthy."""

    systemPicked = Signal(str)
    action = Signal(str)

    def __init__(self, palette: dict[str, str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colours = palette

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setObjectName("header")
        head = QtWidgets.QVBoxLayout(header)
        head.setContentsMargins(22, 16, 22, 14)
        head.setSpacing(1)
        self.title = label("Overview", "pageTitle")
        self.subtitle = label("", "pageSubtitle")
        head.addWidget(self.title)
        head.addWidget(self.subtitle)
        outer.addWidget(header)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(qenum(QtWidgets.QFrame, "Shape", "NoFrame"))
        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(16)

        stats = QtWidgets.QHBoxLayout()
        stats.setSpacing(14)
        self.tiles = {
            "roms": StatTile("ROMs on this card"),
            "used": StatTile("Used by ROMs"),
            "free": StatTile("Free space"),
            "art": StatTile("Missing artwork"),
        }
        for tile in self.tiles.values():
            stats.addWidget(tile)
        layout.addLayout(stats)

        layout.addWidget(label("LIBRARY BY CONSOLE", "sectionLabel"))
        self.bars: dict[str, SystemBar] = {}
        for system in wc.known_systems():
            bar = SystemBar(system, palette, self)
            bar.clicked.connect(self.systemPicked)
            layout.addWidget(bar)
            self.bars[system] = bar

        layout.addWidget(label("CARD HEALTH", "sectionLabel"))
        self.checks = CheckList(palette, self)
        layout.addWidget(self.checks)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(9)
        for key, text, primary in (
            ("doctor", "Run card check", True),
            ("health", "Test the card (write/read)", False),
            ("repair", "Create missing folders", False),
            ("rebuild", "Rebuild all indexes", False),
            ("backup", "Back up saves", False),
            ("open", "Open card folder", False),
            ("eject", "Safely eject", False),
        ):
            button = QtWidgets.QPushButton(text)
            button.setCursor(POINTING_HAND)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def set_summary(self, drive: str, counts: dict[str, int],
                    sizes: dict[str, int], missing_art: int | None,
                    free: int, total: int) -> None:
        self.title.setText(Path(drive).name or drive)
        used = total - free if total else 0
        self.subtitle.setText(
            f"{drive}  ·  {wc.human_size(used)} used of {wc.human_size(total)}"
            if total else drive)
        self.tiles["roms"].set_value(str(sum(counts.values())))
        self.tiles["used"].set_value(wc.human_size(sum(sizes.values())))
        self.tiles["free"].set_value(wc.human_size(free))
        self.tiles["art"].set_value("—" if missing_art is None else str(missing_art))
        largest = max(sizes.values(), default=0)
        for system, bar in self.bars.items():
            bar.set_values(counts.get(system, 0), sizes.get(system, 0), largest)


# ---------------------------------------------------------------------------
# Builder page
# ---------------------------------------------------------------------------


class BuilderPage(QtWidgets.QWidget):
    """Guided, checked, verified SD card build."""

    refreshRequested = Signal()
    checkRequested = Signal()
    buildRequested = Signal()
    healthRequested = Signal()

    def __init__(self, palette: dict[str, str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colours = palette
        self.volumes: list[dict[str, Any]] = []

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setObjectName("header")
        head = QtWidgets.QVBoxLayout(header)
        head.setContentsMargins(22, 16, 22, 14)
        head.setSpacing(1)
        head.addWidget(label("Build a card", "pageTitle"))
        head.addWidget(label(
            "Format the card to FAT32 first with your own disk tools. Wolfpole "
            "checks the volume, downloads and verifies the firmware, writes every "
            "file with a checksum, then reads it all back off the card.",
            "pageSubtitle", wrap=True))
        outer.addWidget(header)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(qenum(QtWidgets.QFrame, "Shape", "NoFrame"))
        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(14)

        # Target -----------------------------------------------------------
        layout.addWidget(label("1  ·  TARGET VOLUME", "sectionLabel"))
        target_row = QtWidgets.QHBoxLayout()
        target_row.setSpacing(9)
        self.target = QtWidgets.QComboBox()
        self.target.setMinimumWidth(420)
        target_row.addWidget(self.target, 1)
        refresh = QtWidgets.QPushButton("Rescan")
        refresh.setCursor(POINTING_HAND)
        refresh.clicked.connect(self.refreshRequested)
        target_row.addWidget(refresh)
        self.show_all = QtWidgets.QCheckBox("Show non-empty volumes too")
        self.show_all.toggled.connect(lambda _: self.refreshRequested.emit())
        target_row.addWidget(self.show_all)
        layout.addLayout(target_row)

        self.target_note = label("", "toolBody", wrap=True)
        layout.addWidget(self.target_note)

        # Firmware ---------------------------------------------------------
        layout.addWidget(label("2  ·  FIRMWARE", "sectionLabel"))
        self.firmware = QtWidgets.QComboBox()
        self.firmware.setMinimumWidth(420)
        layout.addWidget(self.firmware)

        options = QtWidgets.QHBoxLayout()
        options.setSpacing(16)
        self.opt_verify = QtWidgets.QCheckBox("Read every file back and verify")
        self.opt_verify.setChecked(True)
        self.opt_verify.setToolTip(
            "Catches counterfeit cards, bad sectors and truncated writes. "
            "Roughly doubles the time; worth it.")
        self.opt_structure = QtWidgets.QCheckBox("Create the console folders")
        self.opt_structure.setChecked(True)
        self.opt_resume = QtWidgets.QCheckBox("Resume an interrupted build")
        self.opt_resume.setChecked(True)
        for box in (self.opt_verify, self.opt_structure, self.opt_resume):
            options.addWidget(box)
        options.addStretch(1)
        layout.addLayout(options)

        saves_row = QtWidgets.QHBoxLayout()
        saves_row.setSpacing(9)
        self.saves = QtWidgets.QLineEdit()
        self.saves.setPlaceholderText("Optional: restore a save backup ZIP afterwards")
        saves_row.addWidget(self.saves, 1)
        pick = QtWidgets.QPushButton("Choose…")
        pick.setCursor(POINTING_HAND)
        pick.clicked.connect(self._pick_saves)
        saves_row.addWidget(pick)
        layout.addLayout(saves_row)

        # Checks -----------------------------------------------------------
        layout.addWidget(label("3  ·  PRE-FLIGHT", "sectionLabel"))
        self.checks = CheckList(palette, self)
        layout.addWidget(self.checks)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(9)
        self.check_button = QtWidgets.QPushButton("Run checks")
        self.check_button.setCursor(POINTING_HAND)
        self.check_button.clicked.connect(self.checkRequested)
        actions.addWidget(self.check_button)

        self.health_button = QtWidgets.QPushButton("Deep card test")
        self.health_button.setCursor(POINTING_HAND)
        self.health_button.setToolTip(
            "Writes and reads back samples across the whole card. Slow, but it is "
            "how you catch a fake.")
        self.health_button.clicked.connect(self.healthRequested)
        actions.addWidget(self.health_button)

        actions.addStretch(1)
        self.build_button = QtWidgets.QPushButton("Build the card")
        self.build_button.setObjectName("primary")
        self.build_button.setCursor(POINTING_HAND)
        self.build_button.clicked.connect(self.buildRequested)
        actions.addWidget(self.build_button)
        layout.addLayout(actions)

        # Log --------------------------------------------------------------
        layout.addWidget(label("4  ·  PROGRESS", "sectionLabel"))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(170)
        self.log.setPlaceholderText("Build output appears here.")
        layout.addWidget(self.log)

        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def _pick_saves(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a save backup", "", "Save backup (*.zip)")
        if path:
            self.saves.setText(path)

    def set_volumes(self, volumes: list[dict[str, Any]]) -> None:
        self.volumes = volumes
        self.target.clear()
        if not volumes:
            self.target.addItem("No candidate volumes found")
            self.target.setEnabled(False)
            self.target_note.setText(
                "Nothing looks like an SF2000 card. Format a card to FAT32, make sure "
                "it is mounted, then rescan. Tick the box above to include volumes "
                "that already have files on them.")
            return
        self.target.setEnabled(True)
        for volume in volumes:
            state = "empty" if volume["empty"] else f"{volume['entries']} items"
            self.target.addItem(
                f"{volume['mountpoint']}  —  {wc.human_size(volume['total'])}  ·  "
                f"{volume['fstype'] or 'unknown fs'}  ·  {state}")
        self.target_note.setText(
            "Wolfpole never formats a card and never writes to a system volume.")

    def selected_volume(self) -> dict[str, Any] | None:
        index = self.target.currentIndex()
        if 0 <= index < len(self.volumes):
            return self.volumes[index]
        return None

    def set_firmware(self, titles: list[str]) -> None:
        self.firmware.clear()
        if titles:
            self.firmware.addItems(titles)
            self.firmware.setEnabled(True)
        else:
            self.firmware.addItem("Firmware list not loaded")
            self.firmware.setEnabled(False)

    def append(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"{stamp}  {text}")

    def set_busy(self, busy: bool) -> None:
        for widget in (self.build_button, self.check_button, self.health_button,
                       self.target, self.firmware):
            widget.setEnabled(not busy)
        self.build_button.setText("Building…" if busy else "Build the card")


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class ActivityLog(QtWidgets.QDockWidget):
    """Everything Wolfpole has said this session, in one scrollback."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Activity", parent)
        self.setObjectName("activity_dock")
        self.setFeatures(
            qenum(QtWidgets.QDockWidget, "DockWidgetFeature", "DockWidgetMovable")
            | qenum(QtWidgets.QDockWidget, "DockWidgetFeature", "DockWidgetClosable"))

        holder = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(holder)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(7)

        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        layout.addWidget(self.view)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        copy = QtWidgets.QPushButton("Copy all")
        copy.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(self.view.toPlainText()))
        row.addWidget(copy)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self.view.clear)
        row.addWidget(clear)
        layout.addLayout(row)

        self.setWidget(holder)
        self.setMinimumHeight(150)

    def add(self, text: str, tone: str = "info") -> None:
        mark = {"good": "✓", "bad": "✕"}.get(tone, "·")
        self.view.appendPlainText(
            f"{datetime.now().strftime('%H:%M:%S')}  {mark}  {text}")