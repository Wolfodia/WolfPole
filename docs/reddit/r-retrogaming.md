# r/retrogaming

**Title:** Made a safer SF2000 card builder + ROM library tool (Wolfpole) — stops bad cards before you boot

**Body:**

If you use an SF2000, you may know Tadpole/Madpole for managing the SD card. I rebuilt that experience as **Wolfpole** because too many "it won't boot" problems are really bad downloads, fake SD cards, or silent write failures.

Wolfpole's card builder verifies each step and can re-read every extracted file to confirm what's on the card matches what you think you wrote. There's also a deep write/read test to smoke out counterfeit cards before your saves do.

On the library side: cover art grid, shortcut slots, filters for missing artwork, duplicate finder, and live folder watching when you edit the card in Explorer/Finder.

Open source, free, Python-based:
https://github.com/Wolfodia/wolfpole
Overview: https://wolfodia.github.io/wolfpole/
