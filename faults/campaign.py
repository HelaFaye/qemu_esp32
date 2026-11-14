#!/usr/bin/env python3
"""A glitch campaign: fault every instruction of the functions you name, one at a time,
and see what the firmware does each time. The same campaign a lab runs with a glitcher
and thousands of shots, but each fault lands exactly where it's aimed.

    faults/campaign.py --flash flash.bin --elf firmware.elf \\
        --function check_plain --function attempt_plain \\
        --ready "fault target ready" --normal "plain denied" --success "plain GRANTED" \\
        --fault skip --fault zero

Each fault, at each instruction address, the next time the firmware gets there:
  skip     the instruction doesn't run (pc moves past it)
  zero     it runs, and the register it writes comes out 0
  flip:N   it runs, and bit N of the register it writes is flipped
Each outcome, from the first line after the fault matching one of:
  success  --success: the fault bypassed the check (the finding)
  normal   --normal: no effect
  crash    a panic, an exception, a reset
  silent   none of these within --result-timeout, or no normal attempt after (a hang,
           a corrupted stack, a main loop that returned; the chip is reset)
  -        never reached (padding, a path the firmware didn't take)

The board, the console and the reset: firmware/boards.py. Results as a table, and as
JSON with --json.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "firmware"))
from inject import Injector, RSPError  # noqa: E402

CRASH = r"Guru Meditation|abort\(\) was called|Backtrace:|rst:0x|Rebooting\.\.\."
# Mnemonics whose first operand is a source, not what they write
NO_DEST = re.compile(r"^(s8i|s16i|s32i|s32i\.n|s32e|s32c1i|ssi|ssip|ssx|b\w*|j|jx|call\w*|"
                     r"ret\w*|entry|nop\w*|wsr|wur|xsr|isync|dsync|esync|rsync|memw|extw|"
                     r"ill\w*|break\w*|syscall|waiti|loop\w*|ssr|ssl|ssa8\w|ssai|rfe|rfi|"
                     r"movsp|wer|wdtlb|witlb|iitlb|idtlb|dhi|dhwb\w*|dii|diwb\w*|ihi|iii|"
                     r"dpf\w*|ipf\w*|ee\.\w+)$")


def objdump_tool():
    for p in (os.environ.get("XTENSA_OBJDUMP"),
              os.path.expanduser("~/.platformio/packages/toolchain-xtensa-esp32s3/bin/"
                                 "xtensa-esp32s3-elf-objdump"),
              os.path.expanduser("~/.platformio/packages/toolchain-xtensa-esp-elf/bin/"
                                 "xtensa-esp32s3-elf-objdump"),
              "xtensa-esp32s3-elf-objdump", "xtensa-esp-elf-objdump"):
        if p and (os.path.exists(p) or subprocess.run(["which", p], capture_output=True).returncode == 0):
            return p
    raise SystemExit("no Xtensa objdump: set XTENSA_OBJDUMP")


LINE = re.compile(r"^\s*([0-9a-f]{8}):\s+([0-9a-f]+)\s+(\S+)\s*(.*)$")


def _parse(text):
    out = []
    for l in text.splitlines():
        m = LINE.match(l)
        if m:
            addr, raw, mnem, ops = int(m.group(1), 16), m.group(2), m.group(3), m.group(4)
            out.append({"addr": addr, "len": len(raw) // 2, "mnem": mnem,
                        "ops": ops.split("<")[0].strip()})
    return out


def instructions(elf, function, objdump=None):
    """The instructions of a function: objdump's listing, plus listings from every branch
    target inside it (a linear listing loses step after alignment padding)."""
    objdump = objdump or objdump_tool()
    syms = subprocess.run([objdump, "-t", elf], capture_output=True, text=True, check=True).stdout
    m = re.search(r"^([0-9a-f]{8})\s.*\sF\s\S+\s+([0-9a-f]{8})\s+%s$" % re.escape(function), syms, re.M)
    if not m:
        raise SystemExit("%s: no function %s" % (elf, function))
    start, size = int(m.group(1), 16), int(m.group(2), 16)
    end = start + size

    def listing(a):
        return _parse(subprocess.run([objdump, "-d", "--start-address=%#x" % a,
                                      "--stop-address=%#x" % end, elf],
                                     capture_output=True, text=True, check=True).stdout)

    insns = {i["addr"]: i for i in listing(start)}
    todo = set()
    while True:
        for i in list(insns.values()):
            for t in re.findall(r"\b([0-9a-f]{8})\b", i["ops"]):
                t = int(t, 16)
                if start <= t < end and t not in insns:
                    todo.add(t)
        if not todo:
            break
        t = todo.pop()
        for i in listing(t):
            if i["addr"] in insns:
                break                               # Back in step
            insns[i["addr"]] = i
    for i in insns.values():
        i["function"] = function
    return sorted(insns.values(), key=lambda i: i["addr"])


def dest(insn):
    """The register an instruction writes, if it's an a-register it names first."""
    if NO_DEST.match(insn["mnem"]):
        return None
    m = re.match(r"(a\d+)\b", insn["ops"])
    return m.group(1) if m else None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Campaign:
    def __init__(self, node, inj, ready, normal, success, crash=CRASH,
                 reach_timeout=3.0, result_timeout=5.0, log=print):
        self.node, self.inj = node, inj
        self.ready, self.normal, self.success = ready, re.compile(normal), re.compile(success)
        self.crash = re.compile(crash)
        self.reach_timeout, self.result_timeout = reach_timeout, result_timeout
        self.say = log
        # The console: UART0 or the USB serial, whichever the firmware said it was ready
        # on (ESP-IDF writes to both; offsets must be into one of them)
        self.console = node.log_path
        for p in (node.log_path, node.log_path[:-4] + "-usb.log"):
            if re.search(ready, self._read(p)):
                self.console = p
                break

    @staticmethod
    def _read(path):
        try:
            with open(path, errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def text(self):
        return self._read(self.console)

    def _outcome(self, since):
        end = time.time() + self.result_timeout
        rx = re.compile("(%s)|(%s)|(%s)" % (self.success.pattern, self.normal.pattern, self.crash.pattern))
        while time.time() < end:
            m = rx.search(self.text(), since)
            if m:
                return ("success" if m.group(1) else "normal" if m.group(2) else "crash"), m.end()
            time.sleep(0.05)
        return "silent", len(self.text())

    def _recover(self, outcome, since):
        """After a crash or a hang, wait for the firmware to be back (resetting it after a
        hang) so the next fault starts from a running program. since: where in the console
        the crash was seen."""
        if outcome == "silent":
            self.node.qmp("system_reset")
        if outcome in ("silent", "crash"):
            end = time.time() + 30
            while time.time() < end and not re.search(self.ready, self.text()[since:]):
                time.sleep(0.1)

    def one(self, insn, fault):
        since = len(self.text())
        a = insn["addr"]
        if fault == "skip":
            reached = self.inj.skip(a, length=insn["len"], timeout=self.reach_timeout)
        else:
            reg = dest(insn)
            if reg is None:
                return None                 # Writes no a-register: nothing to corrupt
            if fault == "zero":
                reached = self.inj.set_register_after(a, reg, 0, timeout=self.reach_timeout)
            else:
                bit = int(fault.split(":")[1])
                reached = self.inj.flip_register_after(a, reg, bit, timeout=self.reach_timeout)
        if not reached:
            return "-"
        outcome, at = self._outcome(since)
        if outcome in ("normal", "success") and not self._healthy(at):
            # It printed, then stopped (a corrupted stack, a returned main loop): reset it
            self._recover("silent", at)
            return "silent" if outcome == "normal" else outcome
        if outcome in ("crash", "silent"):
            self._recover(outcome, at)
        return outcome

    def _healthy(self, since):
        """Is the firmware still going (another normal attempt after since)?"""
        end = time.time() + self.result_timeout
        while time.time() < end:
            if self.normal.search(self.text(), since):
                return True
            time.sleep(0.05)
        return False

    def run(self, insns, faults):
        results = []
        for insn in insns:
            row = {"addr": "%#010x" % insn["addr"], "function": insn["function"],
                   "insn": ("%s %s" % (insn["mnem"], insn["ops"])).strip()}
            for f in faults:
                row[f] = self.one(insn, f)
            self.say("  %(addr)s %(function)-18s %(insn)-34s " % row +
                     " ".join("%s=%s" % (f, row[f]) for f in faults if row[f] is not None))
            results.append(row)
        return results


def summary(results, faults):
    out = {}
    for f in faults:
        c = {}
        for r in results:
            if r.get(f) is not None:
                c[r[f]] = c.get(r[f], 0) + 1
        out[f] = c
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--board", default="esp32s3-devkitc")
    ap.add_argument("--flash", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--function", action="append", required=True)
    ap.add_argument("--ready", required=True, help="regex: the firmware is up")
    ap.add_argument("--normal", required=True, help="regex: an attempt with no effect")
    ap.add_argument("--success", required=True, help="regex: a bypass")
    ap.add_argument("--crash", default=CRASH)
    ap.add_argument("--fault", action="append", help="skip, zero, flip:N (default: skip)")
    ap.add_argument("--reach-timeout", type=float, default=3.0)
    ap.add_argument("--result-timeout", type=float, default=5.0)
    ap.add_argument("--workdir")
    ap.add_argument("--json")
    a = ap.parse_args()
    faults = a.fault or ["skip"]

    from boards import boot
    work = a.workdir or tempfile.mkdtemp(prefix="campaign-")
    os.makedirs(work, exist_ok=True)
    insns = [i for f in a.function for i in instructions(a.elf, f)]
    port = free_port()
    node = boot(a.board, a.flash, work, name="target", extra=["-gdb", "tcp:127.0.0.1:%d" % port])
    try:
        if not node.wait_for(a.ready, 60):
            raise SystemExit("the firmware didn't come up:\n" + node.log()[-2000:] + node.qemu_output())
        inj = Injector(port)
        print("%d instructions in %s; faults: %s" % (len(insns), ", ".join(a.function), ", ".join(faults)))
        results = Campaign(node, inj, a.ready, a.normal, a.success, a.crash,
                           a.reach_timeout, a.result_timeout).run(insns, faults)
        inj.close()
    except RSPError as e:
        raise SystemExit("%s\n%s" % (e, node.qemu_output()))
    finally:
        node.stop()
    s = summary(results, faults)
    for f in faults:
        print("%-8s %s" % (f, "  ".join("%s %d" % kv for kv in sorted(s[f].items()))))
    if a.json:
        with open(a.json, "w") as fp:
            json.dump({"results": results, "summary": s}, fp, indent=1)
    return results, s


if __name__ == "__main__":
    main()
