#!/usr/bin/env python3
"""Bus errors against faults/bus-target (an MCP23017 written and read back with ESP-IDF's
I2C master driver): clean, then lost ACKs and bit errors (a bus-noise device, patch
0012), then clean again. The driver must report the errors (its error codes, wrong data
seen as wrong), keep working through them, and be error-free once the noise stops.

    faults/bus-target/build.sh OUT && faults/bus_demo.py OUT/flash.bin WORKDIR
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "firmware"))
from boards import boot, bus_noise  # noqa: E402

REPORT = re.compile(r"i2c ops=(\d+) ok=(\d+) nack=(\d+) timeout=(\d+) other=(\d+) mismatch=(\d+)")
FATAL = re.compile(r"Guru Meditation|abort\(\)|Backtrace:")


def main():
    flash, work = sys.argv[1:3]
    os.makedirs(work, exist_ok=True)
    node = boot("esp32s3-devkitc", flash, work, name="bus",
                extra=["-device", "mcp23017,bus=i2c0,address=0x20,id=exp"] +
                bus_noise("noise", "exp", seed=3))
    log = lambda: open(node.log_path, errors="replace").read()   # noqa: E731 (UART0 only)

    def phase(seconds):
        start = len(log())
        time.sleep(seconds)
        text = log()[start:]
        reports = [tuple(map(int, m.groups())) for m in REPORT.finditer(text)]
        total = [sum(r[i] for r in reports) for i in range(6)]
        return reports, dict(zip(("ops", "ok", "nack", "timeout", "other", "mismatch"), total)), text

    failures = []
    try:
        if not node.wait_for("bus target ready", 60):
            raise SystemExit("it didn't start:\n" + log()[-2000:] + node.qemu_output())
        reports, clean, _ = phase(4)
        print("clean:     ", clean)
        if not reports or clean["ok"] != clean["ops"]:
            failures.append("errors without noise")

        before = node.noise("noise", ber=0.002, nack=0.05)
        reports, noisy, text = phase(8)
        counts = node.noise("noise")
        injected, flips = counts["nacks"] - before["nacks"], counts["flips"]
        print("noisy:     ", noisy, "(injected: %d lost ACKs, %d bit errors so far)" % (injected, flips))
        if not reports:
            failures.append("no reports under noise: the driver stopped")
        elif noisy["nack"] == 0 or noisy["mismatch"] == 0:
            failures.append("errors injected but not reported")
        elif noisy["ok"] < noisy["ops"] // 2:
            failures.append("most operations failing at 5% lost ACKs")
        if FATAL.search(text):
            failures.append("a panic under noise")

        node.noise("noise", ber=0, nack=0)
        phase(1)                                       # The report spanning the change
        reports, after, _ = phase(4)
        print("recovered: ", after)
        if not reports or after["ok"] != after["ops"]:
            failures.append("still failing after the noise stopped")
    finally:
        node.stop()
    if failures:
        print("FAIL: " + "; ".join(failures))
        sys.exit(1)
    print("the driver reported the errors, kept going, and recovered")


if __name__ == "__main__":
    main()
