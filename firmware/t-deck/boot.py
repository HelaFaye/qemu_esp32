#!/usr/bin/env python3
"""LilyGO T-Deck's factory firmware on the emulated T-Deck: it finds its display, touch
controller, keyboard and LoRa radio, and draws its UI (a screenshot, checked to be a
picture, not one color). Keys typed and a touch go in; the screen is captured again.

    boot.py FLASH.bin WORKDIR
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from boards import boot  # noqa: E402

CHECKS = [
    ("boots", r"T-DECK factory", 60),
    ("display", r"Init display id:\d+", 30),
    ("touch (GT911)", r"Init GT911 Sensor success", 30),
    ("keyboard", r"Keyboard registered", 30),
    ("LoRa (SX1262)", r"Start Radio success", 60),
]
FATAL = r"Guru Meditation|abort\(\) was called|Backtrace:"


def colors(px, step=1):
    return len({bytes(px[i:i + 3]) for i in range(0, len(px) - 3, 3 * step)})


def save_png(ppm, png):
    try:
        from PIL import Image
        Image.open(ppm).rotate(-90, expand=True).save(png)      # As the T-Deck holds it
    except ImportError:
        pass


def main():
    flash, work = sys.argv[1], sys.argv[2]
    os.makedirs(work, exist_ok=True)
    n = boot("t-deck", flash, work, name="tdeck")
    ok = True
    try:
        for what, rx, timeout in CHECKS:
            m = n.wait_for(rx, timeout)
            print("%-16s %s" % (what, ("ok: " + m.group(0)) if m else "MISSING"))
            ok &= bool(m)
        time.sleep(25)                              # The splash, then the UI
        w, h, px = n.screendump(os.path.join(work, "screen.ppm"))
        c = colors(px)
        print("%-16s %dx%d, %d colors sampled" % ("screen", w, h, c))
        ok &= c > 20
        save_png(os.path.join(work, "screen.ppm"), os.path.join(work, "screen.png"))
        n.qmp("qom-set", path="/machine/peripheral/kbd", property="keys", value="hello")
        n.qmp("qom-set", path="/machine/peripheral/touch", property="x", value=160)
        n.qmp("qom-set", path="/machine/peripheral/touch", property="y", value=120)
        n.qmp("qom-set", path="/machine/peripheral/touch", property="pressed", value=True)
        time.sleep(0.5)
        n.qmp("qom-set", path="/machine/peripheral/touch", property="pressed", value=False)
        time.sleep(3)
        n.screendump(os.path.join(work, "screen-after-input.ppm"))
        save_png(os.path.join(work, "screen-after-input.ppm"), os.path.join(work, "screen-after-input.png"))
    finally:
        n.stop()
    log = n.log()
    bad = re.search(FATAL, log)
    ok &= not bad
    print("\n===== serial (last 80 lines)%s =====" % (" FATAL" if bad else ""))
    print("\n".join(log.splitlines()[-80:]))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
