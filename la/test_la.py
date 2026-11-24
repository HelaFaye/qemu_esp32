"""Tests for la/: VCD in, sigrok sessions in and out, compare."""
import os
import subprocess
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from capture import read_sr, read_vcd, write_sr  # noqa: E402

VCD = """$version qemu esp32s3.gpio logic analyzer $end
$timescale 1 ns $end
$scope module esp32s3 $end
$var wire 1 " GPIO1 $end
$var wire 1 & GPIO5 $end
$upscope $end
$enddefinitions $end
#1000
$dumpvars
0"
1&
$end
#2000
1"
#2500
0"
0&
#11000
"""


def vcd(tmp_path, text=VCD, name="c.vcd"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_read_vcd(tmp_path):
    c = read_vcd(vcd(tmp_path))
    assert c.names == ["GPIO1", "GPIO5"]
    assert c.changes["GPIO1"] == [(0.0, 0), (pytest.approx(1e-6), 1), (pytest.approx(1.5e-6), 0)]
    assert c.changes["GPIO5"] == [(0.0, 1), (pytest.approx(1.5e-6), 0)]
    assert c.duration == pytest.approx(10e-6)


def test_sr_round_trip_and_layout(tmp_path):
    c = read_vcd(vcd(tmp_path))
    out = str(tmp_path / "c.sr")
    write_sr(c, out, rate=24e6, rename={"GPIO1": "D0", "GPIO5": "D1"})
    z = zipfile.ZipFile(out)
    assert z.read("version") == b"2"
    meta = z.read("metadata").decode()
    for line in ("capturefile=logic-1", "total probes=2", "samplerate=24 MHz",
                 "probe1=D0", "probe2=D1", "unitsize=1"):
        assert line in meta
    assert "logic-1-1" in z.namelist()
    back = read_sr(out)
    assert back.names == ["D0", "D1"]
    # Edges land on the nearest 24 MHz sample: 1 us is sample 24, 1.5 us sample 36
    assert back.edges("D0") == [pytest.approx(24 / 24e6), pytest.approx(36 / 24e6)]
    assert back.changes["D1"][0] == (0.0, 1)
    assert back.edges("D1") == [pytest.approx(36 / 24e6)]
    assert back.duration == pytest.approx(10e-6, abs=1 / 24e6)


def test_ten_channels_two_bytes(tmp_path):
    lines = ["$timescale 1 ns $end"]
    lines += ["$var wire 1 %s GPIO%d $end" % (chr(33 + i), i) for i in range(10)]
    lines += ["$enddefinitions $end", "#0", "$dumpvars"] + ["0%s" % chr(33 + i) for i in range(10)]
    lines += ["$end", "#500", "1%s" % chr(33 + 9), "#1000"]
    c = read_vcd(vcd(tmp_path, "\n".join(lines) + "\n"))
    out = str(tmp_path / "ten.sr")
    write_sr(c, out, rate=24e6)
    assert "unitsize=2" in zipfile.ZipFile(out).read("metadata").decode()
    back = read_sr(out)
    assert back.edges("GPIO9") == [pytest.approx(12 / 24e6)]
    assert back.edges("GPIO0") == []


def run_compare(real, emu, *extra):
    return subprocess.run([sys.executable, os.path.join(HERE, "la.py"), "compare", real, emu,
                           "--map", "D0=GPIO1,D1=GPIO5", "--sync", "D0", *extra],
                          capture_output=True, text=True)


def two_segments(shift_ns=0, gap_ns=0):
    """Two sync pulses on GPIO1, each followed by a 1 us pulse on GPIO5. gap_ns moves the
    second segment (CPU time between segments, which resync absorbs); shift_ns moves the
    pulse within its segment (what compare must catch)."""
    s2 = 20000 + gap_ns
    return """$timescale 1 ns $end
$var wire 1 " GPIO1 $end
$var wire 1 & GPIO5 $end
$enddefinitions $end
#0
$dumpvars
0"
0&
$end
#1000
1"
#1100
0"
#%d
1&
#%d
0&
#%d
1"
#%d
0"
#%d
1&
#%d
0&
#40000
""" % (3000 + shift_ns, 4000 + shift_ns, s2, s2 + 100, s2 + 2000 + shift_ns, s2 + 3000 + shift_ns)


def test_compare(tmp_path):
    real = str(tmp_path / "real.sr")
    write_sr(read_vcd(vcd(tmp_path, two_segments(), "r.vcd")), real, rate=24e6,
             rename={"GPIO1": "D0", "GPIO5": "D1"})
    same = vcd(tmp_path, two_segments(gap_ns=7000), "same.vcd")    # Other CPU timing between
    r = run_compare(real, same)
    assert r.returncode == 0, r.stdout
    assert "match" in r.stdout
    off = vcd(tmp_path, two_segments(shift_ns=300), "off.vcd")     # 300 ns late: caught
    r = run_compare(real, off)
    assert r.returncode == 1, r.stdout
    assert "FAIL D1/GPIO5" in r.stdout


def test_fit(tmp_path):
    real = str(tmp_path / "real.sr")
    write_sr(read_vcd(vcd(tmp_path, two_segments(), "r.vcd")), real, rate=24e6,
             rename={"GPIO1": "D0", "GPIO5": "D1"})
    late = vcd(tmp_path, two_segments(shift_ns=300), "late.vcd")
    r = subprocess.run([sys.executable, os.path.join(HERE, "la.py"), "fit", real, late,
                        "--map", "D0=GPIO1,D1=GPIO5", "--sync", "D0"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "segment 0: 2 edges, emulated = 1.000000 x real +300" in r.stdout.replace("+299", "+300").replace("+301", "+300")
