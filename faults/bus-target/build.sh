#!/bin/sh
# Build the bus-error target: OUTDIR/flash.bin (merged, 4 MB) and OUTDIR/firmware.elf
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
(cd "$HERE" && pio run -e target)
B="$HERE/.pio/build/target"
python3 -m esptool --chip esp32s3 merge-bin --flash-mode dio --flash-size 4MB --pad-to-size 4MB \
    -o "$OUT/flash.bin" 0x0 "$B/bootloader.bin" 0x8000 "$B/partitions.bin" 0x10000 "$B/firmware.bin"
cp "$B/firmware.elf" "$OUT/"
