# r/SBCGaming

**Title:** I rewrote the SF2000 card tool (Tadpole/Madpole) with a verified build pipeline — Wolfpole v1.0

**Body:**

I've been using Tadpole/Madpole on my SF2000 for a while, but the card builder always felt like a coin flip: pop a dialog, hope the download finished, hope the card wasn't fake, boot the console and find out.

So I rewrote it as **Wolfpole** — same frogtool/Tadpole lineage, but with a build pipeline that stops at the first stage it can't trust:

- **Pre-flight** — FAT32 only (exFAT hard stop), capacity sanity, empty card, write/read test, measured write speed
- **Download** — resumable with HTTP Range, retries, SHA-256 when published, cached off-card
- **Archive inspection** — zip-slip blocked *before* extraction
- **Extract + verify** — CRC on write, full re-read verification by default (catches counterfeits and bad sectors)
- **Resume** — manifest-backed so interrupted builds don't start over

Beyond building, there's a cover-grid library with live folder watching, shortcut slots, duplicate finder, tidy ROM names, all-systems view, card doctor/rescue tools, and a deep ~48 MB card test for fakes.

It also fixes a bunch of long-standing bugs in the original UI layer (thumbnail memory lifetime, rename hitting wrong rows, delete counting cells instead of ROMs, GUI-thread network I/O, etc.).

**Links**
- Site: https://wolfodia.github.io/wolfpole/
- Repo: https://github.com/Wolfodia/wolfpole
- Python 3 + PyQt5/6, `psutil`, `requests`

Happy to answer questions — especially around the build verification and fake-card detection side.
