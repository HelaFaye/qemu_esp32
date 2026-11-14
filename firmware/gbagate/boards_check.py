#!/usr/bin/env python3
"""gbagate's images on its full boards (firmware/boards.py, from its board.h): what
its own qemu/accurate_boot.py doesn't cover. Each board with an SD card in its slot,
its buttons where board.h puts them:

- every board: all its tasks start, the card mounts, there's a picture, no panic, no
  reset;
- devkit: its encoder comes up (gbagate's default input), and a turn changes the screen;
- supermini: it finds its button expander (0x21 on I2C), and a press of "down" on the
  expander changes the screen.

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
        elif board == "supermini":
            check("it finds its button expander", "on an expander at 0x21" in log,
                  (re.findall(r"buttons_mcp.*", log) or ["no buttons_mcp line"])[0])
            check('a press of "down" on the expander changes the screen',
                  changes_screen(node, "btn_down", work, "down"))
        log = node.log()
        bad = FATAL.search(log)
        check("no panic, abort or watchdog", bad is None, bad.group(0) if bad else "")
        check("no reset after boot", log.count("rst:0x") <= 2)   # UART0 and USB both carry it
        check("QEMU still running", node.proc.poll() is None, node.qemu_output()[-300:])
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
