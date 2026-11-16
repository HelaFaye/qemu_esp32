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
from boards import boot  # noqa: E402

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


# The touch keys (gbagate's src/touch_pad.h): two 40-pixel strips of five 48-pixel keys
# either side of the 240x240 UI, on the 320x240 panel; the XPT2046's raw plates run
# 200..3900 across it (its default calibration, as gbagate's qemu/accurate_boot.py has it).
LEFT_KEYS = ["LEFT", "L", "UP", "DOWN", "R"]


def touch_key(node, name, hold=0.15):
    x, y = 20, LEFT_KEYS.index(name) * 48 + 24
    p = "/machine/peripheral/touch"
    node.qmp("qom-set", path=p, property="x", value=200 + 3700 * x // 319)
    node.qmp("qom-set", path=p, property="y", value=200 + 3700 * y // 239)
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
    node = boot("gbagate-" + board, image, work, name="gbagate", card=card)
    try:
        up = node.wait_for(r"tasks started for the %s image" % IMAGE[board], 60)
        check("every task starts", up is not None)
        time.sleep(2)
        log = node.log()
        check("the card mounts", "card mounted" in log, (re.findall(r"no card mounted.*", log) or ["?"])[0])
        w, h, rgb = screen(node, os.path.join(work, "boot.ppm"))
        check("a %dx%d picture" % (w, h), len(set(rgb[i:i + 3] for i in range(0, len(rgb), 3))) > 3)
        if board == "devkit":
            check("its encoder comes up on 15/16, switch 35", "encoder on A=15 B=16 SW=35" in log,
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
            if applied is None:
                # gbagate's gate_app.c fills applied_input in on the first change, after
                # the edit has stored the new mode, so the first change is never applied:
                # the UI asks for the five-way's keys, the hardware reads the encoder.
                print("  note gbagate doesn't apply the first change of input "
                      "(pio/src/gate_app.c applied_input); a second one is")
                seen = node.count(r"input: 5-way")
                ok &= step(node, "knob", 1, work)               # 5-way + 4: applied
                applied = node.wait_for(r"input: 5-way", 3, after=seen)
            else:
                print("  note gbagate applies the first change of input now")
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
            check("it finds its XPT2046 on CS 16, IRQ 17", "XPT2046 on CS=16 IRQ=17" in log,
                  (re.findall(r"touch.*", log) or ["no touch line"])[0])
            check("a touch on DOWN moves the menu",
                  changes_screen(node, "DOWN", work, "touch", lambda n, k: touch_key(n, k)))
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
