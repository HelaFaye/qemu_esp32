#!/usr/bin/env python3
"""The emulated GPIO's logic analyzer, on the host side.

  la.py sr CAPTURE.vcd OUT.sr [--rate 24e6] [--map GPIO5=D1,...]
      a sigrok session PulseView opens (File > Open), sampled like a real analyzer
  la.py compare REAL.sr EMULATED.vcd --map D0=GPIO1,D1=GPIO2,... --sync D0
      [--tol 100e-9] [--ppm 50]
      edge by edge, channel by channel: each edge's time from the latest sync pulse
      (rising edge of the sync channel) must agree within tol + ppm of that time; the
      same number of edges on each. Exit 1 on a difference.
  la.py fit REAL.sr EMULATED.vcd --map ... --sync D0
      what to correct: per channel and per sync segment, emulated time = slope x real
      time + offset (least squares over paired edges). A slope off 1 is a rate the
      emulation gets wrong (a clock, or -icount's ns per instruction for CPU-timed
      edges: the suggested shift is printed); an offset is a fixed latency to model.
  la.py edges CAPTURE
      each channel's edge count, first and last edge, shortest pulse

A capture is a sigrok session (.sr: PulseView's File > Save) or a VCD (the emulator's
la-file; PulseView imports these too).
"""
import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture import read, write_sr  # noqa: E402


def parse_map(s):
    out = {}
    for pair in filter(None, (s or "").split(",")):
        a, b = pair.split("=")
        out[a.strip()] = b.strip()
    return out


def cmd_sr(a):
    cap = read(a.capture)
    m = parse_map(a.map)
    names = list(m) if m else cap.names
    write_sr(cap, a.out, rate=a.rate, names=names, rename=m)
    print("%s: %d channels, %.6f s at %g MS/s" % (a.out, len(names), cap.duration, a.rate / 1e6))


def cmd_edges(a):
    cap = read(a.capture)
    for n in cap.names:
        e = cap.edges(n)
        widths = [b - x for x, b in zip(e, e[1:])]
        print("%-10s %6d edges  first %s  last %s  shortest %s" % (
            n, len(e), "%.9f" % e[0] if e else "-", "%.9f" % e[-1] if e else "-",
            "%.1f ns" % (min(widths) * 1e9) if widths else "-"))


def rel_to_sync(edges, syncs):
    """(index of the latest sync pulse, time since it) per edge; edges before the first
    sync pulse are dropped."""
    out = []
    for t in edges:
        j = bisect.bisect_right(syncs, t) - 1
        if j >= 0:
            out.append((j, t - syncs[j]))
    return out


def cmd_compare(a):
    real, emu = read(a.real), read(a.emulated)
    m = parse_map(a.map)
    sync_r = a.sync
    sync_e = m.get(sync_r, sync_r)

    def rises(cap, name):
        ch = cap.changes[name]
        return [t for t, lv in ch[1:] if lv == 1]

    sr, se = rises(real, sync_r), rises(emu, sync_e)
    print("sync %s/%s: %d and %d pulses" % (sync_r, sync_e, len(sr), len(se)))
    ok = len(sr) == len(se) and len(sr) > 0
    if not ok:
        print("  FAIL: the sync pulses differ; the captures can't be aligned")
    nsync = min(len(sr), len(se))
    for rn, en in m.items():
        if rn == sync_r:
            continue
        er = [x for x in rel_to_sync(real.edges(rn), sr) if x[0] < nsync]
        ee = [x for x in rel_to_sync(emu.edges(en), se) if x[0] < nsync]
        worst, bad, first_bad = 0.0, 0, None
        for (jr, tr), (je, te) in zip(er, ee):
            limit = a.tol + a.ppm * 1e-6 * max(tr, te)
            d = te - tr if jr == je else float("inf")
            worst = max(worst, abs(d))
            if abs(d) > limit:
                bad += 1
                first_bad = first_bad or (jr, tr, je, te)
        same = len(er) == len(ee)
        good = same and bad == 0
        ok &= good
        print("  %s %s/%s: %d and %d edges, worst %s%s" % (
            "ok  " if good else "FAIL", rn, en, len(er), len(ee),
            "%.1f ns" % (worst * 1e9) if worst != float("inf") else "a segment off",
            "" if not first_bad else "; first off: segment %d at %.3f us, emulated %d at %.3f us"
            % (first_bad[0], first_bad[1] * 1e6, first_bad[2], first_bad[3] * 1e6)))
    print("match" if ok else "DIFFERENT")
    return 0 if ok else 1


def cmd_fit(a):
    import math
    real, emu = read(a.real), read(a.emulated)
    m = parse_map(a.map)
    sr = [t for t, lv in real.changes[a.sync][1:] if lv == 1]
    se = [t for t, lv in emu.changes[m.get(a.sync, a.sync)][1:] if lv == 1]
    pairs_all = []
    for rn, en in m.items():
        if rn == a.sync:
            continue
        er, ee = rel_to_sync(real.edges(rn), sr), rel_to_sync(emu.edges(en), se)
        segs = sorted({j for j, _ in er})
        for j in segs:
            xr = [t for k, t in er if k == j]
            xe = [t for k, t in ee if k == j]
            n = min(len(xr), len(xe))
            if n < 2:
                continue
            xr, xe = xr[:n], xe[:n]
            mx, my = sum(xr) / n, sum(xe) / n
            sxx = sum((x - mx) ** 2 for x in xr)
            slope = sum((x - mx) * (y - my) for x, y in zip(xr, xe)) / sxx if sxx else 1.0
            off = my - slope * mx
            pairs_all.append(slope)
            print("  %s/%s segment %d: %d edges, emulated = %.6f x real %+.1f ns%s" % (
                rn, en, j, n, slope, off * 1e9,
                "" if abs(slope - 1) < 1e-4 else "  (%.0f ppm)" % ((slope - 1) * 1e6)))
    if pairs_all and a.icount is not None:
        mean = sum(pairs_all) / len(pairs_all)
        # -icount shift=N: 2^N ns per instruction; emulated time scales with it
        ns = 2 ** a.icount / mean
        print("for CPU-timed edges: %.3f ns per instruction fits; -icount shift=%d gives %d"
              % (ns, round(math.log2(ns)), 2 ** round(math.log2(ns))))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sr")
    s.add_argument("capture")
    s.add_argument("out")
    s.add_argument("--rate", type=float, default=24e6)
    s.add_argument("--map", help="NAME=LABEL,...: the channels to keep, renamed")
    s.set_defaults(fn=cmd_sr)
    e = sub.add_parser("edges")
    e.add_argument("capture")
    e.set_defaults(fn=cmd_edges)
    c = sub.add_parser("compare")
    c.add_argument("real")
    c.add_argument("emulated")
    c.add_argument("--map", required=True, help="REAL=EMULATED,...")
    c.add_argument("--sync", required=True, help="the real capture's sync channel")
    c.add_argument("--tol", type=float, default=100e-9, help="seconds (default 100 ns)")
    c.add_argument("--ppm", type=float, default=50.0, help="clock error allowed (default 50)")
    c.set_defaults(fn=cmd_compare)
    f = sub.add_parser("fit")
    f.add_argument("real")
    f.add_argument("emulated")
    f.add_argument("--map", required=True)
    f.add_argument("--sync", required=True)
    f.add_argument("--icount", type=int, help="the shift the emulated capture ran with")
    f.set_defaults(fn=cmd_fit)
    a = p.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
