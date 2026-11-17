#!/usr/bin/env python3
"""gbagate's images on its full boards (firmware/boards.py, from its board.h): what
its own qemu/accurate_boot.py doesn't cover. Each board with an SD card in its slot,
its buttons where board.h puts them:

- every board: all its tasks start, the card mounts, there's a picture, no panic, no
  reset;
- devkit: its encoder comes up (gbagate's default input), and a turn changes the screen;
  then, by the encoder alone, Settings > input goes to the five-way, which applies at
  once, and its switches work: B leaves Settings;
- supermini: it finds its button expander (0x21 on I2C), and a press of "down" on the
  expander changes the screen;
- carrier-touch: it finds its XPT2046, and a touch on its DOWN key moves the menu. (The
  board has the Battle Chip reader's two expanders too, but the gate image has no chip
  reader to look for them: that's the full image's.)

    boards_check.py OUTDIR [board ...]      (OUTDIR: build.sh's; default: devkit supermini)
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from boards import boot, gbagate_pins, GBAGATE_PINS  # noqa: E402

IMAGE = {"devkit": "gate", "supermini": "mini", "carrier-touch": "gate"}
FATAL = re.compile(r"Guru Meditation|abort\(\)|Backtrace:|Task watchdog")
fails = 0


def check(what, ok, detail=""):
    global fails
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, " (%s)" % detail if detail and not ok else ""))
    if not ok:
        fails += 1


def make_card(path, rom):
    """A 32 MB FAT card (mtools), with the tester ROM on it if there is one."""
    with open(path, "wb") as f:
        f.truncate(32 << 20)
    subprocess.run(["mformat", "-i", path, "::"], check=True)
    if os.path.exists(rom):
        subprocess.run(["mcopy", "-i", path, rom, "::TESTER.GBA"], check=True)
    return path


def screen(node, path):
    w, h, rgb = node.screendump(path)
    return w, h, bytes(rgb)


def press(node, button, hold=0.15):
    p = "/machine/peripheral/" + button
    node.qmp("qom-set", path=p, property="pressed", value=True)
    time.sleep(hold)
    node.qmp("qom-set", path=p, property="pressed", value=False)


def turn(node, knob, detents=1):
    node.qmp("qom-set", path="/machine/peripheral/" + knob, property="turn", value=detents)


def settled(node, path, quiet=0.4, limit=3.0):
    """The screen once it has stopped changing (two captures `quiet` apart agree)."""
    last = node.screendump(path)[2]
    end = time.time() + limit
    while time.time() < end:
        time.sleep(quiet)
        now = node.screendump(path)[2]
        if now == last:
            return now
        last = now
    return last


def step(node, knob, detents, out):
    """Move the cursor one detent at a time, each seen to move it once the screen has
    settled (a detent can be missed; one seen late must not be turned again)."""
    path = os.path.join(out, "step.ppm")
    for _ in range(abs(detents)):
        before = settled(node, path)
        for _ in range(4):
            turn(node, knob, 1 if detents > 0 else -1)
            if settled(node, path) != before:
                break
        else:
            return False
    return True


# The touch keys: two strips of keys either side of the UI square on the 320x240 panel,
# as gbagate's src/touch_pad.h lays them out and src/touch_pad.c orders them; the
# XPT2046's raw plates run 200..3900 across it (tp_cal_default). Read from the checkout
# when there is one (touch_layout()), else these, which they were at COMMIT.
TOUCH = dict(panel_w=320, panel_h=240, strip_w=40, right_x=280, key_h=48, raw_min=200, raw_max=3900,
             left=["LEFT", "L", "UP", "DOWN", "R"], right=["RIGHT", "A", "B", "SELECT", "START"])


def touch_layout(src):
    """The layout from a gbagate checkout: touch_pad.h's TP_ constants (through the C
    preprocessor) and touch_pad.c's key table; TOUCH's if either can't be read."""
    try:
        consts = ["TP_PANEL_W", "TP_PANEL_H", "TP_STRIP_W", "TP_RIGHT_X", "TP_KEY_H"]
        text = '#include "touch_pad.h"\n' + "".join("v %s %s\n" % (c.lower(), c) for c in consts)
        out = subprocess.run(["cc", "-E", "-P", "-I", os.path.join(src, "src"), "-x", "c", "-"],
                             input=text, capture_output=True, text=True, check=True).stdout
        v = {f[1].upper(): int(eval(f[2], {}, {})) for f in (l.split(None, 2) for l in out.splitlines())
             if len(f) == 3 and f[0] == "v"}
        table = open(os.path.join(src, "src", "touch_pad.c")).read()
        sides = re.findall(r"\[TP_(LEFT|RIGHT)\]\s*=\s*\{(.*?)\}\s*,\s*(?=\[TP_|\})", table, re.S)
        keys = {side: re.findall(r"\{\s*BTN_(\w+)", body) for side, body in sides}
        cal = re.search(r"x_min\s*=\s*(\d+);.*?x_max\s*=\s*(\d+);", table, re.S)
        return dict(panel_w=v["TP_PANEL_W"], panel_h=v["TP_PANEL_H"], strip_w=v["TP_STRIP_W"],
                    right_x=v["TP_RIGHT_X"], key_h=v["TP_KEY_H"],
                    raw_min=int(cal.group(1)) if cal else TOUCH["raw_min"],
                    raw_max=int(cal.group(2)) if cal else TOUCH["raw_max"],
                    left=keys["LEFT"], right=keys["RIGHT"])
    except Exception as e:                      # An older or reorganized checkout
        print("  note the touch layout from the checkout: %s; using the built-in one" % e)
        return dict(TOUCH)


def touch_key(node, name, hold=0.15, layout=TOUCH):
    t = layout
    side = "left" if name in t["left"] else "right"
    x = (t["strip_w"] // 2) if side == "left" else t["right_x"] + t["strip_w"] // 2
    y = t[side].index(name) * t["key_h"] + t["key_h"] // 2
    span = t["raw_max"] - t["raw_min"]
    p = "/machine/peripheral/touch"
    node.qmp("qom-set", path=p, property="x", value=t["raw_min"] + span * x // (t["panel_w"] - 1))
    node.qmp("qom-set", path=p, property="y", value=t["raw_min"] + span * y // (t["panel_h"] - 1))
    node.qmp("qom-set", path=p, property="pressed", value=True)
    time.sleep(hold)
    node.qmp("qom-set", path=p, property="pressed", value=False)


def changes_screen(node, button, out, name, act=press):
    before = screen(node, os.path.join(out, name + "-before.ppm"))
    act(node, button)
    for _ in range(20):                         # A few frames at most
        time.sleep(0.1)
        after = screen(node, os.path.join(out, name + "-after.ppm"))
        if after[2] != before[2]:
            return True
    return False


def run(board, outdir):
    image = os.path.join(outdir, board, "flash.bin")
    work = os.path.join(outdir, "run", "boards-" + board)
    os.makedirs(work, exist_ok=True)
    print("%s (%s image) on its full board" % (board, IMAGE[board]))
    if not os.path.exists(image):
        check("an image to boot", False, image)
        return
    card = make_card(os.path.join(work, "sd.img"), os.path.join(outdir, "src", "rom", "tester.gba"))
    src = os.path.join(outdir, "src")
    pins, layout = None, TOUCH
    if os.path.exists(os.path.join(src, "port", "esp32s3", "board.h")):
        pins = gbagate_pins(src, board)            # board.h's, as built: no copy to drift
        moved = {k: (v, pins.get(k)) for k, v in GBAGATE_PINS[board].items() if pins.get(k) != v}
        if moved:
            print("  note board.h moved %s (boards.py had them as the first); using board.h's" %
                  ", ".join("%s %s->%s" % (k, a, b) for k, (a, b) in moved.items()))
        layout = touch_layout(src)
    p = dict(GBAGATE_PINS[board], **(pins or {}))
    node = boot("gbagate-" + board, image, work, name="gbagate", card=card, pins=pins)
    try:
        up = node.wait_for(r"tasks started for the %s image" % IMAGE[board], 60)
        check("every task starts", up is not None)
        time.sleep(2)
        log = node.log()
        check("the card mounts", "card mounted" in log, (re.findall(r"no card mounted.*", log) or ["?"])[0])
        w, h, rgb = screen(node, os.path.join(work, "boot.ppm"))
        check("a %dx%d picture" % (w, h), len(set(rgb[i:i + 3] for i in range(0, len(rgb), 3))) > 3)
        if board == "devkit":
            enc = "encoder on A=%d B=%d SW=%d" % (p["ENC_A"], p["ENC_B"], p["ENC_SW"])
            check("its encoder comes up (%s)" % enc[11:], enc in log,
                  (re.findall(r"buttons: .*", log) or ["no buttons line"])[0])
            check("a turn of the encoder changes the screen", changes_screen(node, "knob", work, "turn", turn))
            # The menu: Link ... GBA Memory, Settings, Diagnostics, wrapping; a detent
            # forward moves up. The turn above went from Link to Diagnostics.
            ok = step(node, "knob", 1, work)                    # Settings
            press(node, "btn_a")                                # The encoder's switch
            time.sleep(0.8)
            ok &= step(node, "knob", -7, work)                  # theme ... wrap, input
            press(node, "btn_a")                                # Edit
            time.sleep(0.5)
            seen = node.count(r"input: 5-way")
            ok &= step(node, "knob", 1, work)                   # encoder -> 5-way + 5 (wraps)
            applied = node.wait_for(r"input: 5-way", 3, after=seen)
            # The first change must be applied: gbagate before 0da7c4c left it to a
            # second one (pio/src/gate_app.c seeded applied_input after the edit).
            check("by the encoder, Settings > input to the five-way, applied at once",
                  ok and applied is not None, (re.findall(r"input: .*", node.log()) or ["?"])[-1])
            press(node, "btn_a")                                # Done (A: the encoder's pin)
            time.sleep(0.8)
            check("and its switches work: B leaves Settings", changes_screen(node, "btn_b", work, "back"))
        elif board == "supermini":
            check("it finds its button expander", "on an expander at 0x21" in log,
                  (re.findall(r"buttons_mcp.*", log) or ["no buttons_mcp line"])[0])
            check('a press of "down" on the expander changes the screen',
                  changes_screen(node, "btn_down", work, "down"))
        elif board == "carrier-touch":
            xpt = "XPT2046 on CS=%d IRQ=%d" % (p["TOUCH_CS"], p["TOUCH_IRQ"])
            check("it finds its %s" % xpt, xpt in log,
                  (re.findall(r"touch.*", log) or ["no touch line"])[0])
            check("a touch on DOWN moves the menu",
                  changes_screen(node, "DOWN", work, "touch", lambda n, k: touch_key(n, k, layout=layout)))
        log = node.log()
        bad = FATAL.search(log)
        check("no panic, abort or watchdog", bad is None, bad.group(0) if bad else "")
        check("no reset after boot", log.count("rst:0x") <= 2)   # UART0 and USB both carry it
        check("QEMU still running", node.proc.poll() is None, node.qemu_output()[-300:])
        if fails:
            print("  the console's last lines:\n    " + "\n    ".join(log.splitlines()[-25:]))
    finally:
        node.stop()


def main():
    outdir = sys.argv[1]
    for board in sys.argv[2:] or ["devkit", "supermini"]:
        run(board, outdir)
    print("gbagate boards: %s" % ("all checks passed" if not fails else "%d FAILED" % fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
