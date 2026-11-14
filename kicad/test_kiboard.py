"""kiboard against designs with known answers: gbagate's touch carrier as a KiCad board
(fixtures/make_carrier.py), with and without pin names, compared with the hand-written
board in firmware/boards.py; and QEMU accepting what it produces."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "firmware")]
import boards  # noqa: E402
import kiboard  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def wiring(args):
    """What a board is, whatever its ids and chip-select numbers: each device's type and
    bus (and I2C address), and each pin link by device type."""
    types, devs, links = {}, set(), set()
    for kind, a in zip(args[::2], args[1::2]):
        if kind != "-device":
            continue
        fields = a.split(",")
        props = dict(f.split("=", 1) for f in fields[1:] if "=" in f)
        if fields[0] == "esp32s3.pin-link":
            continue
        types[props.get("id")] = fields[0]
        devs.add((fields[0], props.get("bus"), props.get("address", "")) if fields[0] != "gpio-button"
                 else (fields[0], props.get("pin"), ""))
    for kind, a in zip(args[::2], args[1::2]):
        if a.startswith("esp32s3.pin-link,"):
            props = dict(f.split("=", 1) for f in a.split(",")[1:])
            links.add((types.get(props["dev"]), int(props["pin"]), props["line"], props.get("dir", "in"),
                       props.get("open-drain", "off"), props.get("pull", "")))
    return devs, links


@pytest.mark.parametrize("fixture", ["carrier-touch.kicad_pcb", "carrier-touch-nonames.kicad_pcb"])
def test_carrier_matches_the_hand_written_board(fixture):
    machine, args = boards.from_kicad(os.path.join(FIX, fixture), sd="card.img")
    hand_machine, hand = boards.gbagate_carrier_touch(card="card.img")
    assert machine == hand_machine == "esp32s3"
    devs, links = wiring(args)
    hdevs, hlinks = wiring(hand)
    assert devs - hdevs == {("gpio-button", "0", "")}        # The design adds BOOT on GPIO0
    assert hdevs <= devs
    assert links == hlinks
    assert any("drive=kicad_sd" in a for a in args)


def test_report_says_what_it_could_not_do():
    b = kiboard.board_from(os.path.join(FIX, "carrier-touch.kicad_pcb"))
    assert any("LCD_RST" in n and "no GPIO" in n for n in b.notes)


def test_unemulated_chip_is_reported(tmp_path):
    net = tmp_path / "c5.net"
    net.write_text('''(export (version "E")
      (components (comp (ref "U1") (value "ESP32-C5-WROOM-1") (footprint "RF_Module:ESP32-C5-WROOM-1")
                        (libsource (lib "Espressif") (part "ESP32-C5-WROOM-1"))))
      (nets %s))''' % " ".join('(net (code "%d") (name "/N%d") (node (ref "U1") (pin "%d") (pinfunction "IO%d")))'
                               % (i, i, i, i) for i in range(1, 20)))
    b = kiboard.board_from(str(net))
    assert b.machine is None
    assert any("no model" in n for n in b.notes)


@pytest.mark.skipif(not shutil.which(boards.QEMU) and not os.path.exists(boards.QEMU), reason="no QEMU")
def test_qemu_takes_it():
    """QEMU starts with the generated board (every device and link it names exists)."""
    work = tempfile.mkdtemp()
    blank = os.path.join(work, "blank.bin")
    open(blank, "wb").write(b"\xff" * (4 << 20))
    node = boards.boot("kicad:" + os.path.join(FIX, "carrier-touch.kicad_pcb"), blank, work)
    time.sleep(2)
    alive = node.proc.poll() is None
    node.stop()
    assert alive, node.qemu_output()
