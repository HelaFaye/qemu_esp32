#!/bin/sh
# gbagate's own checks of its boards (its qemu/accurate_boot.py, from the checkout
# build.sh made), against this QEMU: the panel, touch, SD card and link PHYs come up;
# the picture is read back from the glass; on the touch carrier a touch moves the menu
# and touch alone reaches Send Program, which lists the program on the card.
# Usage: check.sh OUTDIR (build.sh's); QEMU_XTENSA, and QEMU_BIOS for a QEMU moved
# from its build tree.
set -eu
OUT=$(cd "$1" && pwd)
QEMU=${QEMU_XTENSA:-qemu-system-xtensa}
rc=0
for board in carrier-touch devkit; do
    python3 -I "$OUT/src/qemu/accurate_boot.py" "$QEMU" "$OUT/$board/flash.bin" "$board" gate \
        "$OUT/run/$board" || rc=1
done
exit $rc
