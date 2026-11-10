#!/bin/sh
# Espressif's QEMU at the pinned commit (BASE), with this patch series, built for Xtensa
# (machines esp32 and esp32s3) in $1 (default: ./esp-qemu). Then:
#   $1/build/qemu-system-xtensa -machine esp32s3 ...
#   cd $1/build && QTEST_QEMU_BINARY=./qemu-system-xtensa ./tests/qtest/esp32s3-gpspi-test
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DIR=${1:-esp-qemu}
read -r URL COMMIT < "$HERE/BASE"
if [ ! -d "$DIR/.git" ]; then
    git init -q "$DIR"
    git -C "$DIR" remote add origin "$URL"
fi
git -C "$DIR" fetch -q --depth 1 origin "$COMMIT"
git -C "$DIR" checkout -q -B patched "$COMMIT"
git -C "$DIR" -c user.name=build -c user.email=build@localhost am -q "$HERE"/*.patch
mkdir -p "$DIR/build"
cd "$DIR/build"
[ -f build.ninja ] || ../configure --target-list=xtensa-softmmu --disable-werror \
    --without-default-features --disable-docs --enable-gcrypt --enable-slirp --enable-pixman
ninja qemu-system-xtensa
