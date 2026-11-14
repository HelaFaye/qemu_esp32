#!/usr/bin/env python3
"""The fault-injection demo and its check: faults/target's PIN check, plain and hardened,
under a campaign of skipped instructions and zeroed results.

    faults/target/build.sh OUT && faults/demo.py OUT/flash.bin OUT/firmware.elf WORKDIR

Expected: single faults bypass the plain check (one compare, one branch) and none bypass
the hardened one (spread-out result values, the compare done twice, checked again where
it's used). Exits non-zero if that changes.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def campaign(flash, elf, work, which, functions):
    out = os.path.join(work, which + ".json")
    cmd = [sys.executable, os.path.join(HERE, "campaign.py"), "--flash", flash, "--elf", elf,
           "--ready", "fault target ready", "--normal", "%s denied" % which,
           "--success", "%s GRANTED" % which, "--fault", "skip", "--fault", "zero",
           "--workdir", os.path.join(work, which), "--json", out]
    for f in functions:
        cmd += ["--function", f]
    subprocess.run(cmd, check=True)
    return json.load(open(out))["summary"]


def bypasses(summary):
    return sum(c.get("success", 0) for c in summary.values())


def main():
    flash, elf, work = sys.argv[1:4]
    os.makedirs(work, exist_ok=True)
    plain = campaign(flash, elf, work, "plain", ["check_plain", "attempt_plain"])
    hardened = campaign(flash, elf, work, "hardened", ["check_hardened", "attempt_hardened"])
    print("\nplain:    %s\nhardened: %s" % (plain, hardened))
    ok = bypasses(plain) > 0 and bypasses(hardened) == 0
    print("bypasses: plain %d, hardened %d -> %s" %
          (bypasses(plain), bypasses(hardened), "as expected" if ok else "UNEXPECTED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
