#!/bin/sh
# mytechnotalent/meshcore-bare-esp32s3 at the pinned commit, built twice with PlatformIO:
#   stock/flash.bin     as released (its BLE controller is the S3's own, which QEMU lacks)
#   emulator/flash.bin  the same code with ESP-IDF's NimBLE host over HCI on UART1
#                       (BT_CONTROLLER_DISABLED, BT_NIMBLE_TRANSPORT_UART), so a virtual
#                       controller can stand in for the radio QEMU doesn't have
# Usage: build.sh OUTDIR
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
URL=https://github.com/mytechnotalent/meshcore-bare-esp32s3
COMMIT=$(cat "$HERE/COMMIT")
SRC="$OUT/src"

if [ ! -d "$SRC/.git" ]; then
    git init -q "$SRC"
    git -C "$SRC" remote add origin "$URL"
fi
git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
git -C "$SRC" checkout -q -f "$COMMIT"

merge() {   # build dir, output image
    python3 -m esptool --chip esp32s3 merge-bin --flash-mode dio --flash-size 16MB --pad-to-size 16MB -o "$2" \
        0x0 "$1/bootloader.bin" 0x8000 "$1/partitions.bin" 0x10000 "$1/firmware.bin"
}

build() {   # flavor
    rm -f "$SRC/sdkconfig.bare_meshcore.old"
    (cd "$SRC" && pio run -e bare_meshcore)
    mkdir -p "$OUT/$1"
    merge "$SRC/.pio/build/bare_meshcore" "$OUT/$1/flash.bin"
    cp "$SRC/.pio/build/bare_meshcore/firmware.elf" "$OUT/$1/"
}

git -C "$SRC" checkout -q -- .
build stock

# The emulator flavor: only the Bluetooth controller's place changes
git -C "$SRC" checkout -q -- .
python3 "$HERE/../sdkconfig_set.py" "$SRC/sdkconfig.bare_meshcore" \
    BT_CONTROLLER_ENABLED=n BT_CONTROLLER_DISABLED=y \
    BT_NIMBLE_TRANSPORT_UART=y BT_NIMBLE_TRANSPORT_UART_PORT=1 \
    UART_BAUDRATE_921600=y BT_NIMBLE_HCI_UART_BAUDRATE=921600 \
    BT_NIMBLE_UART_RX_PIN=18 BT_NIMBLE_UART_TX_PIN=17 \
    UART_HW_FLOWCTRL_DISABLE=y
rm -rf "$SRC/.pio/build/bare_meshcore/config"
build emulator
git -C "$SRC" checkout -q -- .
