"""Logic captures: read and write sigrok session files (.sr, what PulseView saves and
opens), read Value Change Dumps (.vcd, what the emulated GPIO's logic analyzer writes),
and turn either into edges per channel.

A capture here is a Capture: channel names, and for each a list of (time in seconds,
level) changes starting with its level at time 0.

sigrok session format (version 2): a zip holding
  version     "2"
  metadata    INI: [global] sigrok version=...; [device 1] capturefile=logic-1,
              total probes=N, samplerate=..., probe1=NAME ..., unitsize=U
  logic-1-1, logic-1-2, ...   the samples, U bytes each, little-endian: bit i is
              probe i+1
"""
import configparser
import io
import re
import zipfile

CHUNK = 4 << 20             # Bytes per logic-1-N member, as libsigrok writes them


class Capture:
    def __init__(self, names, changes, duration):
        self.names = list(names)            # Channel names, in order
        self.changes = changes              # name -> [(t, level)], the first at t=0
        self.duration = duration            # Seconds

    def edges(self, name):
        """Times of the level changes on a channel (not its initial level)."""
        return [t for t, _ in self.changes[name][1:]]

    def level_at(self, name, t):
        lv = self.changes[name][0][1]
        for tc, l in self.changes[name]:
            if tc > t:
                break
            lv = l
        return lv


def _rate_string(hz):
    for unit, div in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if hz >= div and hz % div == 0:
            return "%d %s" % (hz // div, unit)
    return "%d Hz" % hz


def _parse_rate(s):
    m = re.match(r"\s*([\d.]+)\s*([kMG]?)Hz\s*$", s) or re.match(r"\s*([\d.]+)\s*()$", s)
    if not m:
        raise ValueError("samplerate %r" % s)
    return float(m.group(1)) * {"": 1, "k": 1e3, "M": 1e6, "G": 1e9}[m.group(2)]


def read_vcd(path):
    """A VCD's 1-bit wires, times scaled by its timescale."""
    text = open(path).read()
    m = re.search(r"\$timescale\s+(\d+)\s*(s|ms|us|ns|ps|fs)\s+\$end", text)
    scale = int(m.group(1)) * {"s": 1, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12,
                               "fs": 1e-15}[m.group(2)] if m else 1e-9
    ids, names = {}, []
    for vid, name in re.findall(r"\$var\s+\w+\s+1\s+(\S+)\s+(\S+)(?:\s+\[[^\]]*\])?\s+\$end", text):
        ids[vid] = name
        names.append(name)
    body = text[text.index("$enddefinitions"):]
    body = body[body.index("$end") + 4:]
    t0 = None
    t = 0
    changes = {n: [] for n in names}
    for tok in body.split():
        if tok.startswith("#"):
            t = int(tok[1:])
            if t0 is None:
                t0 = t
            continue
        if tok in ("$dumpvars", "$end", "$dumpall", "$dumpon", "$dumpoff"):
            continue
        if tok[0] in "01xXzZ" and tok[1:] in ids:
            level = 1 if tok[0] == "1" else 0
            name = ids[tok[1:]]
            rel = (t - (t0 or 0)) * scale
            ch = changes[name]
            if not ch:
                ch.append((0.0, level))
            elif ch[-1][1] != level:
                ch.append((rel, level))
    end = ((t - (t0 or 0)) * scale) if t0 is not None else 0.0
    for n in names:
        if not changes[n]:
            changes[n].append((0.0, 0))
    return Capture(names, changes, end)


def read_sr(path):
    """A sigrok session's logic channels."""
    z = zipfile.ZipFile(path)
    meta = configparser.ConfigParser(strict=False)
    meta.read_string(z.read("metadata").decode())
    dev = next(s for s in meta.sections() if s.startswith("device"))
    d = meta[dev]
    rate = _parse_rate(d["samplerate"])
    unit = int(d.get("unitsize", "1"))
    nprobes = int(d.get("total probes", "0"))
    names = [d.get("probe%d" % (i + 1)) for i in range(nprobes)]
    used = [i for i, n in enumerate(names) if n]
    cap = d.get("capturefile", "logic-1")
    members = sorted((m for m in z.namelist() if m == cap or m.startswith(cap + "-")),
                     key=lambda m: int(m.rsplit("-", 1)[1]) if m != cap else 0)
    data = b"".join(z.read(m) for m in members)
    n = len(data) // unit
    changes = {names[i]: [] for i in used}
    prev = None
    for k in range(n):
        v = int.from_bytes(data[k * unit:(k + 1) * unit], "little")
        if v == prev:
            continue
        for i in used:
            b = v >> i & 1
            if prev is None:
                changes[names[i]].append((0.0, b))
            elif (prev >> i & 1) != b:
                changes[names[i]].append((k / rate, b))
        prev = v
    return Capture([names[i] for i in used], changes, n / rate)


def read(path):
    return read_sr(path) if path.endswith(".sr") else read_vcd(path)


def write_sr(capture, path, rate=24e6, names=None, rename=None):
    """Sample a capture at `rate` into a sigrok session PulseView opens. names: the
    channels to keep, in order (default all); rename: {name: label} for the file."""
    names = list(names or capture.names)
    unit = 1 if len(names) <= 8 else 2 if len(names) <= 16 else 8
    total = int(round(capture.duration * rate)) + 1
    # Every change, as a sample index, then runs of constant samples between them
    events = []
    for bit, n in enumerate(names):
        for t, lv in capture.changes[n]:
            events.append((int(round(t * rate)), bit, lv))
    events.sort()
    z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
    z.writestr("version", "2")
    meta = ["[global]", "sigrok version=0.5.2", "", "[device 1]", "capturefile=logic-1",
            "total probes=%d" % len(names), "samplerate=%s" % _rate_string(int(rate)),
            "total analog=0"]
    meta += ["probe%d=%s" % (i + 1, (rename or {}).get(n, n)) for i, n in enumerate(names)]
    meta += ["unitsize=%d" % unit, ""]
    z.writestr("metadata", "\n".join(meta))
    buf, chunk_no, value, k, i = io.BytesIO(), 1, 0, 0, 0

    def emit(count, v):
        nonlocal buf, chunk_no
        word = v.to_bytes(unit, "little")
        while count:
            room = (CHUNK - buf.tell()) // unit
            take = min(room, count)
            buf.write(word * take)
            count -= take
            if buf.tell() >= CHUNK:
                z.writestr("logic-1-%d" % chunk_no, buf.getvalue())
                chunk_no += 1
                buf = io.BytesIO()
    while k < total:
        while i < len(events) and events[i][0] <= k:
            _, bit, lv = events[i]
            value = (value & ~(1 << bit)) | (lv << bit)
            i += 1
        nxt = min(events[i][0] if i < len(events) else total, total)
        emit(max(nxt - k, 1) if nxt > k else 1, value)
        k = max(nxt, k + 1)
    if buf.tell():
        z.writestr("logic-1-%d" % chunk_no, buf.getvalue())
    z.close()
