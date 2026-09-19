# r/RetroHandhelds

**Title:** Wolfpole — SF2000 SD manager rewrite with checked card builds, cover grid, and rescue tools

**Body:**

For anyone still wrangling SF2000 cards: I released **Wolfpole v1.0**, a from-scratch UI + builder rewrite of Madpole/Tadpole.

The part I'm most proud of is **Build a card** — it's not "download zip, extract, pray". Each stage has a gate:

1. Pre-flight (filesystem, capacity, empty, access test, speed)
2. Resumable verified download to cache
3. Archive inspection (no zip-slip, strip junk)
4. Extract with per-file CRC + optional full re-read
5. Manifest so builds resume instead of restarting

Also on the page: deep card test (~48 MB spread write/read), card doctor, missing-folder repair, shortcut layout export/import, drag-drop art onto covers, and headless CLI for rebuild/backup.

Lineage credits: Eric Goldstein (Tadpole), faanJD (Madpole), tzlion (frogtool).

- https://wolfodia.github.io/wolfpole/
- https://github.com/Wolfodia/wolfpole

If you've got a card that "worked once then corrupted saves", the write-speed + deep test steps are worth running before you trust it.
