#!/usr/bin/env python3
"""Power failures against faults/power-target (a counter in NVS, committed every 10 ms):
cuts at random times, cuts at the moment a flash write starts, and brown-out dips. After
each, the firmware must come back with the right reset reason, NVS must open, and the
counter must not have lost anything it reported committed.

    faults/power-target/build.sh OUT && faults/power_demo.py OUT/flash.bin OUT/firmware.elf WORKDIR [ROUNDS]
"""
import os
import random
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "firmware"))
from boards import boot  # noqa: E402
from campaign import free_port, objdump_tool  # noqa: E402
from inject import Injector  # noqa: E402

BOOT = re.compile(r"boot reason=(\w+) counter=(\d+)")
COMMITTED = re.compile(r"committed (\d+)")
BAD = re.compile(r"nvs init failed|nvs read failed|nvs needs erase|Guru Meditation|abort\(\)")


def symbol(elf, name):
    nm = objdump_tool().replace("objdump", "nm")
    out = subprocess.run([nm, elf], capture_output=True, text=True, check=True).stdout
    m = re.search(r"^([0-9a-f]+) [Tt] %s$" % re.escape(name), out, re.M)
    if not m:
        raise SystemExit("no %s in %s" % (name, elf))
    return int(m.group(1), 16)


class Console:
    def __init__(self, path):
        self.path, self.pos = path, 0

    def text(self):
        try:
            with open(self.path, errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def wait(self, rx, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            m = rx.search(self.text(), self.pos)
            if m:
                return m
            time.sleep(0.05)
        return None


def main():
    flash, elf, work = sys.argv[1:4]
    rounds = int(sys.argv[4]) if len(sys.argv) > 4 else 12
    os.makedirs(work, exist_ok=True)
    rng = random.Random(1)
    write_addr = symbol(elf, "esp_flash_write")
    port = free_port()
    node = boot("esp32s3-devkitc", flash, work, name="power", extra=["-gdb", "tcp:127.0.0.1:%d" % port])
    con = Console(node.log_path)
    failures = []
    try:
        m = con.wait(BOOT)
        if not m:
            raise SystemExit("it didn't boot:\n" + con.text()[-2000:] + node.qemu_output())
        con.pos = m.end()
        inj = Injector(port)
        kinds = ["cut", "cut-in-flash-write", "brownout"]
        for i in range(rounds):
            kind = kinds[i % len(kinds)]
            if not con.wait(COMMITTED):                # Running again
                failures.append("round %d: no commits before the fault" % i)
                break
            if kind == "cut":
                time.sleep(rng.uniform(0.02, 0.5))
            elif kind == "cut-in-flash-write":
                inj.run_to(write_addr, count=rng.randint(1, 6))
            # The last commit it reported before the fault
            before = [int(x) for x in COMMITTED.findall(con.text(), con.pos)]
            last = before[-1] if before else None
            if kind == "brownout":
                node.brownout(2300, 0.05)
            else:
                node.set_vdd(0)
                if kind == "cut-in-flash-write":
                    inj.cont()                         # (it stays held: no supply)
                time.sleep(0.1)
                node.set_vdd(3300)
            m = con.wait(BOOT, 30)
            if not m:
                failures.append("round %d (%s): didn't come back" % (i, kind))
                break
            reason, counter = m.group(1), int(m.group(2))
            bad = BAD.search(con.text(), con.pos, m.end())
            # It can be one ahead: a commit done but not yet reported
            committed = [int(x) for x in COMMITTED.findall(con.text(), con.pos, m.start())]
            last = committed[-1] if committed else last
            want = "brownout" if kind == "brownout" else "poweron"
            ok = reason == want and not bad and (last is None or last <= counter <= last + 1)
            print("round %2d %-19s last committed %5s, read back %5d, reason %-8s %s" %
                  (i, kind, last, counter, reason, "ok" if ok else "FAIL"))
            if not ok:
                failures.append("round %d (%s): reason %s, last %s, counter %d%s" %
                                (i, kind, reason, last, counter, ", " + bad.group(0) if bad else ""))
            con.pos = m.end()
        inj.close()
    finally:
        node.stop()
    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print("%d power failures, nothing committed lost" % rounds)


if __name__ == "__main__":
    main()
