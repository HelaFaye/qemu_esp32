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
OPTS="--target-list=xtensa-softmmu --disable-werror --without-default-features --disable-docs"
OPTS="$OPTS --enable-gcrypt --enable-slirp --enable-pixman"
# Configure once, and again whenever OPTS change: a build directory from an
# earlier version of this script keeps its options otherwise, and loses what
# they add (screendump needs --enable-pixman).
if [ ! -f "$DIR/build/build.ninja" ] || [ "$(cat "$DIR/build/.configure-opts" 2>/dev/null)" != "$OPTS" ]; then
    rm -rf "$DIR/build"
    mkdir -p "$DIR/build"
    (cd "$DIR/build" && ../configure $OPTS && echo "$OPTS" > .configure-opts)
fi
cd "$DIR/build"
ninja qemu-system-xtensa
