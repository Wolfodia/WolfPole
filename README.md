# Wolfpole

An SF2000 SD card manager — a rewrite of **Madpole** (a fork of **Tadpole**, built on
**tzlion's frogtool**), with an interface built from scratch and a card builder that
actually checks its work.

**Project page:** https://wolfodia.github.io/WolfPole/

| File | Role |
|---|---|
| `wolfpole.py` | Entry point: command line, logging, bootstrap |
| `wolfpole_ui.py` | Interface: shell, cover grid, inspector, builder page, task tray |
| `wolfpole_builder.py` | Card building, verification and repair — no GUI |
| `wolfpole_core.py` | Machinery: Qt shim, config, jobs, drive watcher, cache, model, trash |

## Install

Drop all four files into your Tadpole/Madpole folder — beside `frogtool.py`,
`tadpole_functions.py` and `dialogs/` — and run:

```bash
python wolfpole.py
```

Requires `PyQt5` **or** `PyQt6`, plus `psutil` and `requests`.

---

## Building a card

The original was: pop a message box, shell out to `diskmgmt.msc`, look for a volume
with fewer than two entries in it, then hand the mountpoint and a URL to a
download-and-unzip helper. If the download truncated, if the card was counterfeit, if
the archive held a path escaping the root, or if a write silently failed, you found out
when the console refused to boot.

Wolfpole's **Build a card** page is a checked pipeline, and it stops at the first stage
it cannot trust.

**1 · Pre-flight** inspects the volume before a single byte is written: that it is
mounted and not a system drive; whether the OS calls it removable; that the filesystem
is FAT32 (exFAT is a hard stop — the SF2000 will not boot from it); that capacity is
sane (under ~1 GB is refused, over 128 GB warns, since that is both unsupported by the
firmware and the size counterfeits usually claim); that it is empty; that the unpacked
firmware fits with FAT slack to spare; a real write-and-read-back access test; and a
measured write speed, because a slow card is the single most common cause of SF2000
corruption.

**2 · Download** resumes with HTTP Range, retries with exponential backoff, checks the
byte count against `Content-Length`, verifies SHA-256 when one is published, and lands
in Wolfpole's cache — never on the card being built — so a failed build does not
re-download and a half-written archive can never be mistaken for card contents.

**3 · Archive inspection** runs `testzip`, strips the wrapper folder firmware archives
usually carry, drops `__MACOSX` and `.DS_Store` at any depth, and **refuses zip-slip**:
absolute paths, drive letters and `..` components are rejected before extraction, not
after.

**4 · Extraction** writes each file, flushes, `fsync`s it, and compares the CRC32 it
computed while writing against the archive's own. `zipfile.extractall` verifies CRCs on
*read* and tells you nothing about what landed on the card; this checks the card.

**5 · Verification** (on by default) re-reads every file back off the card and compares
CRCs again. This is the step that catches a counterfeit wrapping writes around a
smaller chip, a bad sector, or a truncated copy — the failure modes that otherwise
appear as a console that will not boot.

**6 · Structure and manifest.** The console folders are created, and a
`wolfpole-build.json` manifest records every file with its CRC — so an interrupted
build **resumes**, skipping what is already verified, instead of starting over.
Optionally restores a save backup ZIP afterwards.

Also on that page: **Deep card test**, which writes ~48 MB of unique data spread across
the card and reads it back. That is the standard way to unmask a fake card, and it
surfaces failing sectors on a genuine one before your saves do.

And in Rescue / Overview: **Card check** (doctor) — missing or truncated `bisrv.asd`,
absent folders, a thin `Resources` folder, ROM indexes older than the ROMs beside them,
low free space, a firmware update still staged in `UpdateFirmware` — plus **Create
missing folders** and a real **Safely eject** that flushes and unmounts per platform.

Nothing here ever formats a drive, and `is_protected()` means no operation can target
`/`, `C:\` or a system folder.

---

## Quality of life

**Overview page.** Per-console bars of count and size, ROM total, space used, how many
covers are missing, card-health results, and one-click backup, rebuild, repair, open and
eject.

**Browse everything at once.** An *All systems* toggle (`Ctrl+Shift+F`) scans every
console into one grid; tiles then show which system each ROM lives in.

**Filter chips.** Everything / No artwork / Shortcuts / Multicore / Large files.
Finding the 40 games still missing covers is now one click.

**Bulk tools.**
- *Tidy up ROM names* — strips `(USA)`, `[!]`, `(Rev A)` and underscores, and moves a
  trailing article, so `Legend of Zelda, The (U) [!]` becomes `The Legend of Zelda`.
  Shows a full preview before renaming anything.
- *Move to another system* — relocates the selection and rebuilds both indexes.
- *Find duplicates* — matches on tidied title plus size across all consoles, reports
  the wasted space, deletes nothing.
- *Export / import shortcut layout* — carry your four home-screen slots to another card.

**Drop artwork onto a cover.** Drag a PNG onto a specific tile and it becomes that
game's art. Drop ROMs anywhere else and they get copied in.

**Live folder watching.** Edit the card in Explorer or Finder and the grid catches up by
itself, debounced, and never while Wolfpole is mid-write.

**Activity log** (`Ctrl+L`) — every message this session, copyable in one click for
when you ask the Discord for help.

**Session restore** — last card, console, view mode, filter chip, scope and cover size
all come back.

**Low-space warning** when a card drops under 4% free, because that is when saves start
failing silently.

**More keys:** `F2` rename, `Ctrl+C` copy paths, `Ctrl+D` duplicates, `Ctrl+E` eject,
`Ctrl+B` builder, `Ctrl+1`/`Ctrl+2` grid/list.

---

## The interface

No menu bar. A sidebar with a card chip (capacity ring, free space, click to switch),
one row per console with a live count, and the destinations: Overview, Device,
Multicore, Build, Rescue. Below them a **task tray** — work runs in threads and reports
there, so the window never blocks and you can browse one console while firmware
installs elsewhere. Results arrive as **toasts**, sometimes with a button (Undo after a
delete, Eject after a build).

The library is a **cover grid**, because box art is how anyone recognises a ROM, with a
dense list one click away. The four home-screen shortcut slots are **tiles** above it:
select a ROM, click a slot; taking an occupied slot moves it and says so. Selecting a
ROM slides in an **inspector** with large art, facts, slot chips and per-ROM actions.

Everything is reachable from the **command palette** (`Ctrl+K`).

## Keyboard

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `Ctrl+K` | Command palette | | `F2` | Rename |
| `Ctrl+F` | Search | | `Ctrl+C` | Copy paths |
| `Ctrl+Shift+F` | All-systems scope | | `Ctrl+D` | Find duplicates |
| `Ctrl+O` | Add ROMs | | `Ctrl+B` | Build a card |
| `Ctrl+T` | Add artwork | | `Ctrl+E` | Safely eject |
| `F5` / `Shift+F5` | Rebuild one / all | | `Ctrl+L` | Activity log |
| `Del` / `Ctrl+Z` | Delete / undo | | `Ctrl+1` `Ctrl+2` | Grid / list |

## What was wrong underneath

1. `loadROMsToTable` built a `QImage` over a local `bytearray` that was immediately
   garbage-collected. `QImage` does not copy its buffer, so every thumbnail pointed at
   freed memory.
2. `catchTableCellChanged` called `itemAt(col, row)` — pixel coordinates — so renaming
   edited the wrong row, or nothing.
3. `deleteAllSelectedROMs` iterated `selectedItems()`, one item **per cell**: three
   selected ROMs meant fifteen delete attempts.
4. `bootloaderPatch` recursed on a checksum mismatch, unbounded.
5. Every network call and all disk I/O ran on the GUI thread; `loadMenus()` fetched
   three JSON catalogues while building the menu bar.
6. The drive poll cleared the combo box every second, dropping the selection.
7. Box art was scraped by feeding GitHub's HTML to BeautifulSoup.
8. Logging was configured twice, both into `os.getcwd()`.

## What's underneath now

- A job system over `QThreadPool`, with callbacks connected as *explicitly queued* — a
  plain callable has no receiver `QObject`, so Qt would otherwise run the slot on the
  worker thread.
- Thumbnails decode off-thread as `QImage` and convert to `QPixmap` on the GUI thread,
  in an LRU keyed by path + mtime + size.
- A model/view library: sorting by byte count, not by the text "1.5 KB".
- A drive watcher thread that emits only on genuine change.
- Undoable deletion via a trash bin, and atomic temp-file-then-replace writes.
- Box art through GitHub's contents API, cached, matched to ROMs you actually have.
- PyQt5 or PyQt6, one codebase.

## Command line

```bash
python wolfpole.py --list-drives
python wolfpole.py --drive E:\ --rebuild ALL
python wolfpole.py --drive E:\ --backup-saves ~/backups
python wolfpole.py --portable --theme dark --offline --verbose
```

Headless commands never import the UI, so they run over SSH and in scheduled tasks.

Credits stand: Eric Goldstein (Tadpole), faanJD (Madpole), tzlion (frogtool),
wikkiewikkie and Jason Grieves, and osaka on the RetroHandhelds Discord.