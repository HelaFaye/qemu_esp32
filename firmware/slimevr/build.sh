#!/bin/sh
# HelaFaye/SlimeVR-Tracker-ESP (a fork of SlimeVR's tracker firmware) at the pinned
# commit, its BOARD_ESP32S3_SUPERMINI build, with two changes to its configuration only:
#   - the pioarduino platform (Arduino-ESP32 3.3), the core its ESP32-C5 boards use,
#     where the S3 env pins espressif32 6.7.0 with Arduino 3.0.1;
#   - Wi-Fi credentials for the emulated access point's open network, "Open Wifi".
# Usage: build.sh OUTDIR        -> OUTDIR/flash.bin, OUTDIR/firmware.elf
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
URL=https://github.com/HelaFaye/SlimeVR-Tracker-ESP
COMMIT=$(cat "$HERE/COMMIT")
SRC="$OUT/src"
ENV=BOARD_ESP32S3_SUPERMINI
PLATFORM=https://github.com/pioarduino/platform-espressif32/releases/download/55.03.312-1/platform-espressif32.zip

if [ ! -d "$SRC/.git" ]; then
    git init -q "$SRC"
    git -C "$SRC" remote add origin "$URL"
fi
git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
git -C "$SRC" checkout -q -f "$COMMIT"

python3 - "$SRC/platformio.ini" "$ENV" "$PLATFORM" <<'PY'
import re, sys
path, env, platform = sys.argv[1:]
s = open(path).read()
head = "[env:%s]\n" % env
start = s.index(head) + len(head)
end = s.find("\n[", start)
section = s[start:end]
section = re.sub(r"^platform = .*$", "platform = " + platform, section, count=1, flags=re.M)
section = re.sub(r"^platform_packages =\n(  .*\n)*", "", section, flags=re.M)
s = s[:start] + section + s[end:]
s = s.replace("  -std=gnu++2a\n",
              "  -std=gnu++2a\n  -DWIFI_CREDS_SSID='\"Open Wifi\"'\n  -DWIFI_CREDS_PASSWD='\"\"'\n", 1)
open(path, "w").write(s)
PY

(cd "$SRC" && pio run -e "$ENV")
B="$SRC/.pio/build/$ENV"
python3 -m esptool --chip esp32s3 merge-bin --flash-mode dio --flash-size 4MB --pad-to-size 4MB \
    -o "$OUT/flash.bin" 0x0 "$B/bootloader.bin" 0x8000 "$B/partitions.bin" 0x10000 "$B/firmware.bin"
cp "$B/firmware.elf" "$OUT/"
git -C "$SRC" checkout -q -- .
