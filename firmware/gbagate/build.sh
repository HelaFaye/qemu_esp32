#!/bin/sh
# HelaFaye/gbagate at the pinned commit (COMMIT): its gate image for the touch carrier
# and for the devkit, and its mini image for the Supermini, as its own tools/build_hw.sh
# builds them (ESP-IDF 5.5; source
# $IDF_PATH/export.sh first, or run in the espressif/idf:v5.5.6 container).
#   OUTDIR/src                  the checkout (check.sh runs its qemu/accurate_boot.py),
#                               with its tester ROM built (arm-none-eabi-gcc)
#   OUTDIR/<board>/flash.bin    bootloader, partitions and app, padded to 4 MB
#   OUTDIR/<board>/gbagate.elf  for addr2line
# Usage: build.sh OUTDIR
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
URL=https://github.com/HelaFaye/gbagate
COMMIT=$(cat "$HERE/COMMIT")
SRC="$OUT/src"

if [ ! -d "$SRC/.git" ]; then
    git init -q "$SRC"
    git -C "$SRC" remote add origin "$URL"
fi
git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
git -C "$SRC" checkout -q -f "$COMMIT"

# The tester ROM the touch carrier's SD card holds: built, not committed
# (gbagate's own CI builds it the same way). Needs arm-none-eabi-gcc.
make -C "$SRC" rom

for target in carrier-touch:gate devkit:gate supermini:mini; do
    board=${target%:*} variant=${target#*:}
    (cd "$SRC" && BOARD=$board tools/build_hw.sh "$variant")
    dir="$SRC/build/hw-$variant-$board"
    mkdir -p "$OUT/$board"
    # merge_bin and --fill-flash-size: esptool 4 (ESP-IDF 5.5) knows only these, and 5 still takes them
    (cd "$dir" && python3 -m esptool --chip esp32s3 merge_bin --fill-flash-size 4MB \
        -o "$OUT/$board/flash.bin" @flash_args)
    cp "$dir/gbagate.elf" "$OUT/$board/"
done
