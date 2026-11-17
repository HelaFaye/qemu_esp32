#!/bin/sh
# Espressif's QEMU at the pinned commit (BASE), with this patch series, built for Xtensa
# (machines esp32 and esp32s3) in $1 (default: ./esp-qemu). Then:
#   $1/build/qemu-system-xtensa -machine esp32s3 ...
#   cd $1/build && QTEST_QEMU_BINARY=./qemu-system-xtensa ./tests/qtest/esp32s3-gpspi-test
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DIR=${1:-esp-qemu}
read -r URL COMMIT < "$HERE/BASE"

normalize_url() {
    case "$1" in
        git@github.com:*) URL_VALUE="https://github.com/${1#git@github.com:}" ;;
        ssh://git@github.com/*) URL_VALUE="https://github.com/${1#ssh://git@github.com/}" ;;
        *) URL_VALUE=$1 ;;
    esac
    printf '%s\n' "$URL_VALUE" | sed 's/\.git$//'
}

# This directory is a disposable QEMU source/build workspace, not an arbitrary
# Git checkout. Refuse a different remote or local changes rather than resetting
# a caller's branch or overwriting files.
if git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1; then
    ACTUAL_URL=$(git -C "$DIR" remote get-url origin 2>/dev/null || true)
    if [ -z "$ACTUAL_URL" ] || [ "$(normalize_url "$ACTUAL_URL")" != "$(normalize_url "$URL")" ]; then
        echo "error: $DIR is a Git repository, but origin is '$ACTUAL_URL' (expected '$URL')" >&2
        exit 1
    fi
else
    if [ -e "$DIR" ]; then
        if [ ! -d "$DIR" ] || [ -n "$(find "$DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
            echo "error: $DIR exists and is not an empty Git workspace; refusing to initialize it" >&2
            exit 1
        fi
    else
        mkdir -p "$DIR"
    fi
    git init -q "$DIR"
    git -C "$DIR" remote add origin "$URL"
fi

WORKSPACE=$(cd "$DIR" && pwd -P)
GIT_ROOT=$(git -C "$DIR" rev-parse --show-toplevel)
if [ "$GIT_ROOT" != "$WORKSPACE" ]; then
    echo "error: $DIR resolves inside another Git work tree ($GIT_ROOT); refusing to use it" >&2
    exit 1
fi

if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=all)" ]; then
    echo "error: $DIR has local changes or untracked files; refusing to reset it" >&2
    exit 1
fi

git -C "$DIR" fetch -q --depth 1 origin "$COMMIT"
# Work detached at the pinned base. Do not use checkout -B: that can move an
# existing caller-owned branch named "patched" on every build.
git -C "$DIR" checkout -q --detach "$COMMIT"
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
