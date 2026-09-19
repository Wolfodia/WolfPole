"""
wolfpole_builder.py — reliable SD card building, verification and repair.

The original "Build Fresh SD Card" was: pop a message, shell out to
``diskmgmt.msc``, look for a volume with fewer than two entries, then hand the
mountpoint and a URL to a download-and-unzip helper. If the download truncated,
if the card was a counterfeit, if the archive contained a path escaping the
root, or if a write silently failed, you found out when the console refused to
boot.

This module makes each of those a step that can be checked:

  preflight   inspect the volume before writing a single byte
  health      write/read-back sampling that catches fake and failing cards
  download    resumable, retried, size- and hash-verified, cached off-card
  extract     zip-slip proof, per-file CRC checked as it is written, fsynced
  verify      re-read every file back off the card and compare CRCs
  structure   create the folder set the SF2000 firmware expects
  manifest    record what was written so an interrupted build can resume

Nothing here imports the GUI. Every long function takes a JobContext so it can
report progress and be cancelled.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import random
import shutil
import string
import subprocess
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import wolfpole_core as wc
from wolfpole_core import JobContext

log = logging.getLogger(f"{wc.APP_SLUG}.builder")

MANIFEST_NAME = "wolfpole-build.json"

#: Entries an otherwise-empty volume is allowed to contain.
VOLUME_NOISE = {
    "system volume information", "$recycle.bin", ".trashes", ".spotlight-v100",
    ".fseventsd", ".temporaryitems", ".documentrevisions-v100", "lost+found",
    "desktop.ini", "autorun.inf", ".ds_store", "found.000",
}

#: Directories the SF2000 firmware expects to exist.
BASE_DIRS = ("bios", "Resources", "save", "UpdateFirmware")

FAT_TYPES = {"fat32", "fat", "vfat", "msdos", "exfat", "dos"}

MIN_CARD_BYTES = 900 * 1024 * 1024          # below this it is not an SF2000 card
LARGE_CARD_BYTES = 129 * 1024 * 1024 * 1024  # firmware gets unhappy past ~128 GB
SLOW_CARD_BYTES_PER_SEC = 1.5 * 1024 * 1024


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class Check:
    key: str
    title: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != FAIL


@dataclass
class Report:
    target: str
    checks: list[Check] = field(default_factory=list)

    def add(self, key: str, title: str, status: str, detail: str = "") -> Check:
        check = Check(key, title, status, detail)
        self.checks.append(check)
        return check

    @property
    def fatal(self) -> bool:
        return any(check.status == FAIL for check in self.checks)

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status == WARN]

    def summary(self) -> str:
        bad = sum(1 for c in self.checks if c.status == FAIL)
        warn = len(self.warnings)
        if bad:
            return f"{bad} problem(s), {warn} warning(s)"
        if warn:
            return f"looks usable, {warn} warning(s)"
        return "all checks passed"


# ---------------------------------------------------------------------------
# Volume inspection
# ---------------------------------------------------------------------------


def partition_for(mountpoint: str) -> Any | None:
    psutil = wc.optional_import("psutil")
    if psutil is None:
        return None
    try:
        for partition in psutil.disk_partitions(all=False):
            if os.path.normcase(partition.mountpoint) == os.path.normcase(mountpoint):
                return partition
    except Exception as exc:  # noqa: BLE001
        log.debug("disk_partitions failed: %s", exc)
    return None


def volume_entries(mountpoint: str) -> list[str]:
    """Visible entries on a volume, ignoring the junk every OS leaves behind."""
    try:
        return [name for name in os.listdir(mountpoint)
                if name.lower() not in VOLUME_NOISE]
    except OSError as exc:
        log.debug("Cannot list %s: %s", mountpoint, exc)
        return []


def looks_removable(mountpoint: str) -> bool | None:
    """True/False when we can tell, None when we cannot."""
    partition = partition_for(mountpoint)
    if partition is None:
        return None
    options = (getattr(partition, "opts", "") or "").lower()
    if "removable" in options:
        return True
    if platform.system() == "Windows":
        return "removable" in options
    device = (getattr(partition, "device", "") or "")
    if device.startswith("/dev/sd") or device.startswith("/dev/mmc") \
            or device.startswith("/dev/disk"):
        return None
    return None


def candidate_volumes(include_non_empty: bool = False) -> list[dict[str, Any]]:
    """Volumes that could plausibly be an SF2000 card."""
    psutil = wc.optional_import("psutil")
    found: list[dict[str, Any]] = []
    if psutil is None:
        return found
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("disk_partitions failed: %s", exc)
        return found

    for partition in partitions:
        mount = partition.mountpoint
        if not mount or wc.is_protected(mount):
            continue
        free, total = wc.free_space(mount)
        if not total or total < MIN_CARD_BYTES or total > 2 * LARGE_CARD_BYTES:
            continue
        entries = volume_entries(mount)
        empty = not entries
        if not empty and not include_non_empty:
            continue
        found.append({
            "mountpoint": mount,
            "fstype": (getattr(partition, "fstype", "") or "").lower(),
            "total": total,
            "free": free,
            "empty": empty,
            "entries": len(entries),
            "froggy": wc.looks_froggy(mount),
            "removable": looks_removable(mount),
        })
    found.sort(key=lambda item: (not item["empty"], item["total"]))
    return found


def measure_write_speed(ctx: JobContext, mountpoint: str,
                        megabytes: int = 8) -> float:
    """Bytes per second, measured with a real fsynced write."""
    payload = os.urandom(1024 * 1024)
    probe = Path(mountpoint) / f".wolfpole-speed-{os.getpid()}.tmp"
    started = time.perf_counter()
    try:
        with open(probe, "wb") as handle:
            for i in range(megabytes):
                ctx.check()
                handle.write(payload)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        elapsed = max(1e-6, time.perf_counter() - started)
        return (megabytes * 1024 * 1024) / elapsed
    finally:
        probe.unlink(missing_ok=True)


def preflight(ctx: JobContext, mountpoint: str, required_bytes: int = 0,
              require_empty: bool = True, quick: bool = False) -> Report:
    """Inspect a volume before writing anything to it."""
    report = Report(mountpoint)
    path = Path(mountpoint)

    ctx.status("Checking the volume…")
    if not path.is_dir():
        report.add("exists", "Volume is mounted", FAIL,
                   f"{mountpoint} is not a readable directory.")
        return report
    report.add("exists", "Volume is mounted", PASS, mountpoint)

    if wc.is_protected(mountpoint):
        report.add("protected", "Not a system drive", FAIL,
                   "This is a system or root volume. Wolfpole will not write here.")
        return report
    report.add("protected", "Not a system drive", PASS)

    removable = looks_removable(mountpoint)
    if removable is True:
        report.add("removable", "Removable media", PASS)
    elif removable is False:
        report.add("removable", "Removable media", WARN,
                   "The operating system reports this as a fixed disk. Make very "
                   "sure it is the card and not an internal drive.")
    else:
        report.add("removable", "Removable media", WARN,
                   "Could not tell whether this is removable. Check the mountpoint "
                   "matches your card reader.")

    partition = partition_for(mountpoint)
    fstype = (getattr(partition, "fstype", "") or "").lower() if partition else ""
    if not fstype:
        report.add("fs", "Filesystem", WARN,
                   "The filesystem type could not be read. The SF2000 needs FAT32.")
    elif "fat32" in fstype or fstype in ("fat", "vfat", "msdos", "dos"):
        report.add("fs", "Filesystem is FAT32", PASS, fstype)
    elif "exfat" in fstype:
        report.add("fs", "Filesystem", FAIL,
                   "This card is exFAT. The SF2000 only boots from FAT32 — "
                   "reformat before continuing.")
    else:
        report.add("fs", "Filesystem", FAIL,
                   f"This card is {fstype}. The SF2000 needs FAT32.")

    free, total = wc.free_space(mountpoint)
    if total < MIN_CARD_BYTES:
        report.add("size", "Card capacity", FAIL,
                   f"{wc.human_size(total)} is too small for the firmware.")
    elif total > LARGE_CARD_BYTES:
        report.add("size", "Card capacity", WARN,
                   f"{wc.human_size(total)} — cards larger than 128 GB are known to "
                   "misbehave on the SF2000. A 32–128 GB card is the safe range.")
    else:
        report.add("size", "Card capacity", PASS, wc.human_size(total))

    entries = volume_entries(mountpoint)
    if require_empty and entries:
        report.add("empty", "Card is empty", FAIL,
                   f"{len(entries)} item(s) already here, starting with "
                   f"{', '.join(entries[:3])}. Format the card first, or choose "
                   "the update path instead of a fresh build.")
    elif entries:
        report.add("empty", "Existing contents", WARN,
                   f"{len(entries)} item(s) present; matching files will be replaced.")
    else:
        report.add("empty", "Card is empty", PASS)

    if required_bytes:
        # Extraction needs the unpacked size plus headroom for FAT slack.
        needed = int(required_bytes * 1.08)
        if free < needed:
            report.add("space", "Free space", FAIL,
                       f"Needs about {wc.human_size(needed)}, only "
                       f"{wc.human_size(free)} free.")
        else:
            report.add("space", "Free space", PASS,
                       f"{wc.human_size(free)} free, about {wc.human_size(needed)} needed")

    ctx.status("Testing write access…")
    probe = path / f".wolfpole-probe-{os.getpid()}"
    try:
        probe.write_bytes(b"wolfpole")
        readback = probe.read_bytes()
        probe.unlink()
        if readback != b"wolfpole":
            report.add("write", "Write test", FAIL,
                       "Data read back from the card did not match what was written.")
        else:
            report.add("write", "Write test", PASS)
    except OSError as exc:
        report.add("write", "Write test", FAIL, f"Cannot write to the card: {exc}")
        return report

    if not quick:
        ctx.status("Measuring write speed…")
        try:
            speed = measure_write_speed(ctx, mountpoint)
        except wc.Cancelled:
            raise
        except OSError as exc:
            report.add("speed", "Write speed", WARN, f"Could not measure: {exc}")
        else:
            rate = f"{speed / (1024 * 1024):.1f} MB/s"
            if speed < SLOW_CARD_BYTES_PER_SEC:
                report.add("speed", "Write speed", WARN,
                           f"{rate} is very slow. Cheap or worn cards are the most "
                           "common cause of SF2000 corruption.")
            else:
                report.add("speed", "Write speed", PASS, rate)

    return report


def health_check(ctx: JobContext, mountpoint: str, sample_mb: int = 48) -> Report:
    """Write/read-back sampling across the card.

    Counterfeit cards report a large capacity and silently wrap writes around a
    much smaller flash chip. Writing markers spread across the claimed capacity
    and reading them back is the standard way to catch that, and it also
    surfaces failing sectors on a genuine but worn card.
    """
    report = Report(mountpoint)
    free, total = wc.free_space(mountpoint)
    if not total:
        report.add("read", "Card readable", FAIL, "Capacity could not be read.")
        return report

    folder = Path(mountpoint) / ".wolfpole-health"
    chunk_size = 4 * 1024 * 1024
    chunks = max(4, min(sample_mb // 4, max(4, int(free * 0.6) // chunk_size)))
    written: list[tuple[Path, bytes]] = []

    try:
        folder.mkdir(parents=True, exist_ok=True)
        ctx.status("Writing test data across the card…")
        write_started = time.perf_counter()
        for i in range(chunks):
            ctx.progress(i, chunks * 2, f"writing sample {i + 1} of {chunks}")
            seed = bytes(random.getrandbits(8) for _ in range(64))
            payload = (seed * (chunk_size // 64))[:chunk_size]
            target = folder / f"sample-{i:03d}.bin"
            with open(target, "wb") as handle:
                handle.write(payload)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            written.append((target, seed))
        write_seconds = max(1e-6, time.perf_counter() - write_started)

        # Drop anything the OS is caching so the read really touches the card.
        _drop_caches()

        ctx.status("Reading it back…")
        read_started = time.perf_counter()
        bad: list[str] = []
        for i, (target, seed) in enumerate(written):
            ctx.progress(chunks + i, chunks * 2, f"verifying sample {i + 1} of {chunks}")
            try:
                data = target.read_bytes()
            except OSError as exc:
                bad.append(f"{target.name}: unreadable ({exc})")
                continue
            expected = (seed * (chunk_size // 64))[:chunk_size]
            if data != expected:
                bad.append(f"{target.name}: data came back different")
        read_seconds = max(1e-6, time.perf_counter() - read_started)

        volume = chunks * chunk_size
        report.add("write_speed", "Write speed", PASS,
                   f"{volume / write_seconds / (1024 * 1024):.1f} MB/s")
        report.add("read_speed", "Read speed", PASS,
                   f"{volume / read_seconds / (1024 * 1024):.1f} MB/s")

        if bad:
            report.add("integrity", "Data integrity", FAIL,
                       f"{len(bad)} of {chunks} samples came back wrong. This card is "
                       "either counterfeit or failing — do not trust it with saves.\n\n"
                       + "\n".join(bad[:6]))
        else:
            report.add("integrity", "Data integrity", PASS,
                       f"{chunks} samples ({wc.human_size(volume)}) written and read "
                       "back identically")

        if total > LARGE_CARD_BYTES:
            report.add("capacity", "Claimed capacity", WARN,
                       f"{wc.human_size(total)} claimed. Cards this large are both "
                       "unsupported by the firmware and the usual size faked by "
                       "counterfeits.")
        else:
            report.add("capacity", "Claimed capacity", PASS, wc.human_size(total))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return report


def _drop_caches() -> None:
    """Best-effort attempt to stop the OS answering reads from RAM."""
    try:
        if platform.system() == "Linux":
            os.sync()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


class DownloadError(RuntimeError):
    pass


def download(ctx: JobContext, url: str, destination: Path,
             expected_sha256: str = "", attempts: int = 4) -> Path:
    """Fetch a file with resume, retries and verification.

    The archive lands in Wolfpole's cache, never on the card being built, so a
    failed build does not have to re-download and a half-written archive can
    never be mistaken for card contents.
    """
    requests = wc.optional_import("requests")
    if requests is None:
        raise DownloadError("The requests library is not available.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    if destination.exists() and expected_sha256:
        ctx.status("Checking the cached download…")
        if wc.sha256_file(destination) == expected_sha256:
            ctx.status("Using the verified copy already in the cache.")
            return destination
        destination.unlink(missing_ok=True)

    last_error = ""
    for attempt in range(1, attempts + 1):
        ctx.check()
        already = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={already}-"} if already else {}
        try:
            with requests.get(url, stream=True, timeout=45, headers=headers) as response:
                if already and response.status_code == 200:
                    # Server ignored the range request; start over.
                    already = 0
                    partial.unlink(missing_ok=True)
                elif already and response.status_code != 206:
                    response.raise_for_status()
                else:
                    response.raise_for_status()

                total = int(response.headers.get("Content-Length", 0)) + already
                mode = "ab" if already else "wb"
                done = already
                with open(partial, mode) as handle:
                    for block in response.iter_content(chunk_size=1 << 18):
                        ctx.check()
                        if not block:
                            continue
                        handle.write(block)
                        done += len(block)
                        if total:
                            ctx.progress(done, total,
                                         f"downloaded {wc.human_size(done)} of "
                                         f"{wc.human_size(total)}")
                        else:
                            ctx.status(f"downloaded {wc.human_size(done)}")
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass

                if total and done < total:
                    raise DownloadError(
                        f"connection closed after {wc.human_size(done)} of "
                        f"{wc.human_size(total)}")
            break
        except wc.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - retry anything transient
            last_error = str(exc)
            log.warning("Download attempt %s/%s failed: %s", attempt, attempts, exc)
            if attempt == attempts:
                raise DownloadError(
                    f"The download failed after {attempts} attempts: {last_error}")
            delay = min(20, 2 ** attempt)
            ctx.status(f"retrying in {delay}s…")
            for _ in range(delay * 5):
                ctx.check()
                time.sleep(0.2)

    if expected_sha256:
        ctx.status("Verifying the download…")
        digest = wc.sha256_file(partial)
        if digest != expected_sha256:
            partial.unlink(missing_ok=True)
            raise DownloadError(
                "The download does not match its published checksum, so it is "
                "corrupt or has been tampered with. Nothing was written to the card.")

    os.replace(partial, destination)
    return destination


# ---------------------------------------------------------------------------
# Archive handling
# ---------------------------------------------------------------------------


@dataclass
class ArchivePlan:
    entries: list[zipfile.ZipInfo]
    total_bytes: int
    root_prefix: str

    @property
    def count(self) -> int:
        return len(self.entries)


def _is_junk(name: str) -> bool:
    """Archive noise that must never reach the card.

    Checked per path component, because firmware archives usually wrap
    everything in a folder — so the junk arrives as ``pack/__MACOSX/…`` rather
    than at the top level.
    """
    parts = [part.lower() for part in name.replace("\\", "/").split("/") if part]
    if any(part == "__macosx" for part in parts):
        return True
    return bool(parts) and parts[-1] in {".ds_store", "thumbs.db", "desktop.ini"}


def _common_root(names: Iterable[str]) -> str:
    """Firmware archives sometimes wrap everything in one folder; strip it."""
    tops = {name.split("/", 1)[0] for name in names if "/" in name}
    singles = {name for name in names if "/" not in name}
    if len(tops) == 1 and not singles:
        return next(iter(tops)) + "/"
    return ""


def inspect_archive(path: Path) -> ArchivePlan:
    """Read the archive's index and reject anything that escapes the root."""
    with zipfile.ZipFile(path) as archive:
        broken = archive.testzip()
        if broken is not None:
            raise RuntimeError(f"The archive is damaged (bad entry: {broken}).")
        names = [info.filename for info in archive.infolist() if not _is_junk(info.filename)]
        prefix = _common_root(names)
        entries = []
        for info in archive.infolist():
            if _is_junk(info.filename) or info.is_dir():
                continue
            relative = info.filename[len(prefix):] if prefix else info.filename
            if not relative:
                continue
            # Zip-slip guard: no absolute paths, no drive letters, no climbing out.
            normalised = relative.replace("\\", "/")
            if normalised.startswith("/") or ".." in Path(normalised).parts \
                    or (len(normalised) > 1 and normalised[1] == ":"):
                raise RuntimeError(
                    f"The archive tries to write outside the card ({info.filename}). "
                    "Wolfpole refused to extract it.")
            entries.append(info)
        total = sum(info.file_size for info in entries)
    return ArchivePlan(entries, total, prefix)


def extract(ctx: JobContext, archive_path: Path, destination: Path,
            plan: ArchivePlan | None = None,
            manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract with a CRC check on every file as it is written.

    ``zipfile.extractall`` verifies CRCs on read but tells you nothing about
    what actually landed on the card. Here each file is written, flushed,
    fsynced, and its CRC compared against the archive's before moving on.
    """
    plan = plan or inspect_archive(archive_path)
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, Any] = (manifest or {}).get("files", {}) if manifest else {}
    results = {"files": {}, "skipped": 0, "written": 0, "failed": []}

    done_bytes = 0
    with zipfile.ZipFile(archive_path) as archive:
        for index, info in enumerate(plan.entries):
            ctx.check()
            relative = info.filename[len(plan.root_prefix):] if plan.root_prefix \
                else info.filename
            target = destination / relative
            label = f"{index + 1}/{plan.count}  {relative}"
            ctx.progress(done_bytes, plan.total_bytes, label)

            previous = written.get(relative)
            if previous and previous.get("crc") == info.CRC and target.exists() \
                    and target.stat().st_size == info.file_size:
                results["skipped"] += 1
                results["files"][relative] = previous
                done_bytes += info.file_size
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            crc = 0
            try:
                with archive.open(info) as source, open(target, "wb") as sink:
                    while True:
                        ctx.check()
                        block = source.read(1 << 18)
                        if not block:
                            break
                        sink.write(block)
                        crc = zlib.crc32(block, crc)
                        done_bytes += len(block)
                        ctx.progress(done_bytes, plan.total_bytes, label)
                    sink.flush()
                    try:
                        os.fsync(sink.fileno())
                    except OSError:
                        pass
            except wc.Cancelled:
                raise
            except OSError as exc:
                results["failed"].append(f"{relative}: {exc}")
                continue

            if crc != info.CRC:
                results["failed"].append(
                    f"{relative}: checksum mismatch while writing")
                continue

            results["written"] += 1
            results["files"][relative] = {
                "crc": info.CRC, "size": info.file_size,
                "written": datetime.now().isoformat(timespec="seconds"),
            }
    flush_volume(destination)
    return results


def verify(ctx: JobContext, archive_path: Path, destination: Path,
           plan: ArchivePlan | None = None) -> list[str]:
    """Read every file back off the card and compare CRCs.

    This is the step that catches a counterfeit card wrapping writes, a
    truncated copy, or a bad sector — the failure modes that otherwise show up
    as a console that will not boot.
    """
    plan = plan or inspect_archive(archive_path)
    problems: list[str] = []
    _drop_caches()
    done = 0
    for index, info in enumerate(plan.entries):
        ctx.check()
        relative = info.filename[len(plan.root_prefix):] if plan.root_prefix \
            else info.filename
        target = destination / relative
        ctx.progress(done, plan.total_bytes, f"verifying {index + 1}/{plan.count}  {relative}")
        if not target.exists():
            problems.append(f"{relative}: missing from the card")
            done += info.file_size
            continue
        try:
            size = target.stat().st_size
            if size != info.file_size:
                problems.append(
                    f"{relative}: {wc.human_size(size)} on the card, expected "
                    f"{wc.human_size(info.file_size)}")
                done += info.file_size
                continue
            crc = 0
            with open(target, "rb") as handle:
                while True:
                    ctx.check()
                    block = handle.read(1 << 18)
                    if not block:
                        break
                    crc = zlib.crc32(block, crc)
                    done += len(block)
            if crc != info.CRC:
                problems.append(f"{relative}: contents differ from the archive")
        except OSError as exc:
            problems.append(f"{relative}: could not be read back ({exc})")
    return problems


def flush_volume(destination: Path) -> None:
    """Push the OS's write cache out to the card."""
    try:
        if hasattr(os, "sync"):
            os.sync()
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        # Windows: opening and fsyncing a scratch file nudges the volume cache.
        probe = destination / ".wolfpole-flush"
        with open(probe, "wb") as handle:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Structure, manifest, eject
# ---------------------------------------------------------------------------


def ensure_structure(destination: Path) -> list[str]:
    """Create the folders the firmware expects, without touching existing ones."""
    created = []
    for name in list(BASE_DIRS) + wc.known_systems():
        folder = destination / name
        if not folder.exists():
            try:
                folder.mkdir(parents=True, exist_ok=True)
                created.append(name)
            except OSError as exc:
                log.warning("Could not create %s: %s", folder, exc)
    return created


def read_manifest(destination: Path) -> dict[str, Any]:
    path = destination / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_manifest(destination: Path, payload: dict[str, Any]) -> None:
    try:
        with wc.atomic_open(destination / MANIFEST_NAME) as handle:
            json.dump(payload, handle, indent=1)
    except OSError as exc:
        log.warning("Could not write the build manifest: %s", exc)


def safe_eject(mountpoint: str) -> tuple[bool, str]:
    """Flush and unmount, so the card is safe to pull out."""
    flush_volume(Path(mountpoint))
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["diskutil", "eject", mountpoint], check=True,
                           capture_output=True, timeout=30)
            return True, "Card ejected."
        if system == "Linux":
            for command in (["udisksctl", "unmount", "-b"], ["umount"]):
                try:
                    subprocess.run(command + [mountpoint], check=True,
                                   capture_output=True, timeout=30)
                    return True, "Card unmounted."
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            return False, "Could not unmount automatically; use your file manager."
        if system == "Windows":
            drive = os.path.splitdrive(mountpoint)[0] or mountpoint
            script = (
                "$v=New-Object -comObject Shell.Application;"
                f"$v.Namespace(17).ParseName('{drive}').InvokeVerb('Eject')"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           check=True, capture_output=True, timeout=30)
            return True, "Eject requested. Wait for Windows to confirm."
    except subprocess.TimeoutExpired:
        return False, "The eject command timed out."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not eject automatically: {exc}"
    return False, "Ejecting is not supported on this platform."


# ---------------------------------------------------------------------------
# Card doctor: inspect a card that already exists
# ---------------------------------------------------------------------------


def doctor(ctx: JobContext, mountpoint: str) -> Report:
    """Check an existing card for the faults that stop an SF2000 booting."""
    report = Report(mountpoint)
    root = Path(mountpoint)

    ctx.status("Looking for the firmware…")
    bisrv = root / "bios" / "bisrv.asd"
    if not bisrv.is_file():
        report.add("bisrv", "Firmware present", FAIL,
                   "bios/bisrv.asd is missing. The console cannot boot without it.")
    else:
        size = bisrv.stat().st_size
        if size < 4 * 1024 * 1024:
            report.add("bisrv", "Firmware present", FAIL,
                       f"bisrv.asd is only {wc.human_size(size)} — it is truncated.")
        else:
            version = ""
            tf = wc.optional_import("tadpole_functions")
            getter = getattr(tf, "bisrv_getFirmwareVersion", None) if tf else None
            if callable(getter):
                try:
                    version = str(getter(str(bisrv)) or "")
                except Exception as exc:  # noqa: BLE001
                    log.debug("Version read failed: %s", exc)
            report.add("bisrv", "Firmware present", PASS,
                       version or wc.human_size(size))

    ctx.status("Checking the folder structure…")
    missing = [name for name in BASE_DIRS if not (root / name).is_dir()]
    missing += [name for name in wc.known_systems() if not (root / name).is_dir()]
    if missing:
        report.add("dirs", "Folder structure", WARN,
                   "Missing: " + ", ".join(missing) + ". Wolfpole can create these.")
    else:
        report.add("dirs", "Folder structure", PASS)

    if not (root / "Resources").is_dir():
        report.add("resources", "Resources folder", FAIL,
                   "The Resources folder holds the UI images; without it the "
                   "console shows a black screen.")
    else:
        count = len(list((root / "Resources").glob("*")))
        report.add("resources", "Resources folder", PASS if count > 10 else WARN,
                   f"{count} files")

    ctx.status("Checking the ROM indexes…")
    stale = []
    for system in wc.known_systems():
        folder = root / system
        if not folder.is_dir():
            continue
        index = folder / "xfgle.hqk"
        roms = [p for p in folder.glob("*") if p.is_file() and p.name != "xfgle.hqk"]
        if roms and not index.exists():
            stale.append(f"{system} has {len(roms)} ROMs but no index")
        elif index.exists() and roms and index.stat().st_mtime < max(
                p.stat().st_mtime for p in roms):
            stale.append(f"{system} index is older than its ROMs")
    if stale:
        report.add("index", "ROM indexes", WARN,
                   "\n".join(stale) + "\n\nRebuild every system to fix this.")
    else:
        report.add("index", "ROM indexes", PASS)

    free, total = wc.free_space(mountpoint)
    if total and free / total < 0.03:
        report.add("space", "Free space", WARN,
                   f"Only {wc.human_size(free)} left. Saves can fail to write on a "
                   "full card.")
    elif total:
        report.add("space", "Free space", PASS, f"{wc.human_size(free)} free")

    leftovers = root / "UpdateFirmware"
    if leftovers.is_dir() and any(leftovers.iterdir()):
        report.add("staged", "Staged firmware update", WARN,
                   "UpdateFirmware still holds a pending patch. Boot the console "
                   "to apply it, or delete the folder if it already ran.")
    else:
        report.add("staged", "Staged firmware update", PASS)

    return report


# ---------------------------------------------------------------------------
# The build pipeline
# ---------------------------------------------------------------------------


@dataclass
class BuildOptions:
    mountpoint: str
    url: str
    title: str = "firmware"
    sha256: str = ""
    verify_after_write: bool = True
    create_structure: bool = True
    require_empty: bool = True
    restore_saves_from: str = ""
    resume: bool = True


@dataclass
class BuildResult:
    ok: bool
    stage: str
    message: str
    preflight: Report | None = None
    written: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)


def build_card(ctx: JobContext, options: BuildOptions, cache_dir: Path) -> BuildResult:
    """Download, write, verify. Stops at the first stage that cannot be trusted."""
    destination = Path(options.mountpoint)

    # 1. Preflight --------------------------------------------------------
    ctx.status("Running pre-flight checks…")
    report = preflight(ctx, options.mountpoint, require_empty=options.require_empty)
    if report.fatal:
        failed = next(c for c in report.checks if c.status == FAIL)
        return BuildResult(False, "preflight",
                           f"{failed.title}: {failed.detail}", report)

    # 2. Download ---------------------------------------------------------
    ctx.status("Fetching the firmware archive…")
    name = options.url.split("/")[-1].split("?")[0] or "firmware.zip"
    if not name.lower().endswith(".zip"):
        name += ".zip"
    archive = cache_dir / "firmware" / name
    try:
        download(ctx, options.url, archive, options.sha256)
    except wc.Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        return BuildResult(False, "download", str(exc), report)

    # 3. Inspect ----------------------------------------------------------
    ctx.status("Reading the archive…")
    try:
        plan = inspect_archive(archive)
    except Exception as exc:  # noqa: BLE001
        return BuildResult(False, "inspect", str(exc), report)
    if not plan.count:
        return BuildResult(False, "inspect", "The archive contains no files.", report)

    free, _ = wc.free_space(options.mountpoint)
    if free < plan.total_bytes * 1.05:
        return BuildResult(
            False, "space",
            f"The firmware needs about {wc.human_size(int(plan.total_bytes * 1.05))} "
            f"but only {wc.human_size(free)} is free.", report)

    # 4. Extract ----------------------------------------------------------
    manifest = read_manifest(destination) if options.resume else {}
    if manifest.get("source") != options.url:
        manifest = {}
    ctx.status(f"Writing {plan.count} files…")
    try:
        written = extract(ctx, archive, destination, plan, manifest)
    except wc.Cancelled:
        write_manifest(destination, {
            "source": options.url, "title": options.title, "complete": False,
            "when": datetime.now().isoformat(timespec="seconds"),
            "files": manifest.get("files", {}),
        })
        raise
    except Exception as exc:  # noqa: BLE001
        return BuildResult(False, "extract", str(exc), report)

    if written["failed"]:
        write_manifest(destination, {
            "source": options.url, "title": options.title, "complete": False,
            "when": datetime.now().isoformat(timespec="seconds"),
            "files": written["files"],
        })
        return BuildResult(
            False, "extract",
            f"{len(written['failed'])} file(s) could not be written correctly.",
            report, written["written"], written["skipped"], written["failed"])

    # 5. Verify -----------------------------------------------------------
    problems: list[str] = []
    if options.verify_after_write:
        ctx.status("Reading everything back off the card…")
        problems = verify(ctx, archive, destination, plan)
        if problems:
            write_manifest(destination, {
                "source": options.url, "title": options.title, "complete": False,
                "when": datetime.now().isoformat(timespec="seconds"),
                "files": written["files"],
            })
            return BuildResult(
                False, "verify",
                f"{len(problems)} file(s) did not read back correctly. The card is "
                "not trustworthy — try a different one.",
                report, written["written"], written["skipped"], problems)

    # 6. Structure and saves ---------------------------------------------
    if options.create_structure:
        ctx.status("Creating the folder structure…")
        ensure_structure(destination)

    if options.restore_saves_from:
        ctx.status("Restoring saves…")
        try:
            with zipfile.ZipFile(options.restore_saves_from) as saves:
                for info in saves.infolist():
                    if info.is_dir() or _is_junk(info.filename):
                        continue
                    relative = info.filename.replace("\\", "/")
                    if relative.startswith("/") or ".." in Path(relative).parts:
                        continue
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with saves.open(info) as source, open(target, "wb") as sink:
                        shutil.copyfileobj(source, sink)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Saves could not be restored: {exc}")

    # 7. Finish -----------------------------------------------------------
    write_manifest(destination, {
        "source": options.url,
        "title": options.title,
        "complete": True,
        "when": datetime.now().isoformat(timespec="seconds"),
        "wolfpole": wc.APP_VERSION,
        "files": written["files"],
    })
    flush_volume(destination)

    verified = " and verified" if options.verify_after_write else ""
    return BuildResult(
        True, "done",
        f"{written['written']} files written{verified}"
        + (f", {written['skipped']} already correct" if written["skipped"] else "")
        + ".",
        report, written["written"], written["skipped"], problems)