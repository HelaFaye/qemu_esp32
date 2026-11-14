#!/bin/sh
# Xinyuan-LilyGO/T-Deck at the pinned commit: the UnitTest example (the factory firmware:
# LVGL UI, display, touch, keyboard, LoRa, SD, GPS, microphone), built with PlatformIO as
# the repository sets it up, merged into one 16 MB flash image.
# Usage: build.sh OUTDIR
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
COMMIT=$(cat "$HERE/COMMIT")
SRC="$OUT/src"
if [ ! -d "$SRC/.git" ]; then
    git init -q "$SRC"
    git -C "$SRC" remote add origin https://github.com/Xinyuan-LilyGO/T-Deck
fi
GIT_LFS_SKIP_SMUDGE=1 git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
git -C "$SRC" checkout -q -f "$COMMIT"
(cd "$SRC" && pio run -e T-Deck)
B="$SRC/.pio/build/T-Deck"
APP0=$(ls -d "$HOME"/.platformio/packages/framework-arduinoespressif32/tools/partitions)/boot_app0.bin
python3 -m esptool --chip esp32s3 merge-bin --flash-mode dio --flash-size 16MB --pad-to-size 16MB \
    -o "$OUT/flash.bin" 0x0 "$B/bootloader.bin" 0x8000 "$B/partitions.bin" 0xe000 "$APP0" 0x10000 "$B/firmware.bin"
cp "$B/firmware.elf" "$OUT/"
