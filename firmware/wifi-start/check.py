#!/usr/bin/env python3
"""Wi-Fi start-up on the emulated ESP32-S3: the minimal app (src/main.c) gets through
the PHY's calibrations and the MAC's reset, its station starts, and a scan completes
(finding nothing: no frames move).

    firmware/wifi-start/build.sh OUT && firmware/wifi-start/check.py OUT/flash.bin WORKDIR
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from boards import boot  # noqa: E402

CHECKS = [("Wi-Fi initialized", r"wifi: init ESP_OK"),
          ("PHY calibrated", r"phy_init: Saving new calibration data[^\n]*"),
          ("station started", r"wifi: station started"),
          ("scan completes", r"wifi: scan done, \d+ networks")]


def main():
    flash, work = sys.argv[1:3]
    os.makedirs(work, exist_ok=True)
    n = boot("esp32s3-devkitc", flash, work, name="wifi")
    ok = True
    try:
        for what, rx in CHECKS:
            m = n.wait_for(rx, 60)
            print("%-18s %s" % (what, ("ok: " + m.group(0)) if m else "MISSING"))
            ok &= bool(m)
    finally:
        n.stop()
    bad = re.search(r"Guru Meditation|abort\(\)|Backtrace:", n.log())
    print("%-18s %s" % ("no panic", "ok" if not bad else "FAIL: " + bad.group(0)))
    if not ok or bad:
        print("\n".join(n.log().splitlines()[-30:]))
    sys.exit(0 if ok and not bad else 1)


if __name__ == "__main__":
    main()
