#!/usr/bin/env python3
"""LilyGO T-Display-S3's factory firmware on the emulated board (firmware/boards.py):
its LVGL UI goes out over the 8-bit parallel bus (LCD_CAM, GDMA) to the ST7789.

- its boot animation reaches the glass: a picture, not one color, at 170x320;
- Wi-Fi starts and scans (it finds nothing: no access point is emulated), and it waits
  out its connect and SmartConfig timeouts (~3 minutes), then carries on without network;
- its UI comes up; a press of its KEY button (GPIO14) moves to its next page
  (most of the screen changes: not the clock's colon blinking). Slowly: with no
  clock set its loop reads the buttons, and draws, only every 5 s, as on the board;
- no panic, no reset.

    check.py FLASH.bin WORKDIR
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from boards import boot  # noqa: E402

FATAL = re.compile(r"Guru Meditation|abort\(\) was called|Backtrace:|Task watchdog")
fails = 0


def check(what, ok, detail=""):
    global fails
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, " (%s)" % detail if detail and not ok else ""))
    if not ok:
        fails += 1
    return ok


def colors(rgb):
    return len({bytes(rgb[i:i + 3]) for i in range(0, len(rgb) - 2, 3)})


def save_png(ppm):
    try:
        from PIL import Image
        Image.open(ppm).rotate(90, expand=True).save(ppm[:-4] + ".png")   # As it's held, USB left
    except ImportError:
        pass


def changed_share(a, b):
    """The share of pixels clearly different (a channel off by more than 64): a new
    page, not a redraw's small shifts in shade nor the clock's colon blinking."""
    n = min(len(a), len(b)) // 3
    return sum(max(abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]), abs(a[i + 2] - b[i + 2])) > 64
               for i in range(0, 3 * n, 3)) / n if n else 0


def shot(node, work, name):
    path = os.path.join(work, name + ".ppm")
    w, h, rgb = node.screendump(path)
    save_png(path)
    return w, h, bytes(rgb)


def main():
    flash, work = sys.argv[1], sys.argv[2]
    os.makedirs(work, exist_ok=True)
    node = boot("t-display-s3", flash, work, name="tdisplay")
    try:
        print("T-Display-S3 factory firmware")
        check("it boots", node.wait_for(r"tone \.\.\.", 60) is not None)
        w, h, rgb = shot(node, work, "logo")
        check("its boot animation is on the glass (%dx%d, %d colors)" % (w, h, colors(rgb)),
              (w, h) == (170, 320) and colors(rgb) > 3)
        check("Wi-Fi starts and scans", node.wait_for(r"scan done", 60) is not None)
        check("it carries on without network",
              node.wait_for(r"WiFi not connected, continue without network", 300) is not None)
        time.sleep(4)                                   # Its 2 s pause, then its UI
        before = shot(node, work, "ui")
        check("its UI is on the glass (%d colors)" % colors(before[2]), colors(before[2]) > 3)
        # With no clock set (no network; so on the real board too), each pass of its loop
        # waits 5 s in getLocalTime(), then runs freely for ~100 ms: the buttons are read,
        # and LVGL drawn, only in those windows. A click takes three windows to be one
        # (down, up, then 400 ms with no second press), so: hold KEY 4.8 s, which spans a
        # window ~96% of the time but is one short press within it, then give it 45 s.
        # A press no window saw is tried again; pressing again sooner would be a double
        # click, which the firmware ignores.
        p = "/machine/peripheral/btn_key"
        changed = False
        for _ in range(3):
            node.qmp("qom-set", path=p, property="pressed", value=True)
            time.sleep(4.8)
            node.qmp("qom-set", path=p, property="pressed", value=False)
            end = time.time() + 45
            while time.time() < end:
                time.sleep(0.5)
                after = shot(node, work, "ui-next")
                if changed_share(before[2], after[2]) > 0.15:
                    time.sleep(15)                  # The slide, a step a window: let it end
                    after = shot(node, work, "ui-next")
                    changed = changed_share(before[2], after[2]) > 0.15
                    break
            if changed:
                break
        check("a press of KEY moves to the next page", changed)
        log = node.log()
        bad = FATAL.search(log)
        check("no panic, abort or watchdog", bad is None, bad.group(0) if bad else "")
        check("no reset after boot", log.count("rst:0x") <= 2, "%d" % log.count("rst:0x"))
        check("QEMU still running", node.proc.poll() is None, node.qemu_output()[-300:])
        if fails:
            print("  the console's last lines:\n    " + "\n    ".join(log.splitlines()[-25:]))
    finally:
        node.stop()
    print("T-Display-S3: %s" % ("all checks passed" if not fails else "%d FAILED" % fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
