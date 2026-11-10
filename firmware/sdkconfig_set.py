#!/usr/bin/env python3
"""Set options in an ESP-IDF sdkconfig: sdkconfig_set.py FILE NAME=VALUE ...

NAME is without CONFIG_; VALUE n writes "# CONFIG_NAME is not set". Options already in the
file are replaced in place, others appended; ESP-IDF's build resolves the rest (choices,
dependencies) when it next runs menuconfig's defaults.
"""
import re
import sys


def main():
    path, settings = sys.argv[1], sys.argv[2:]
    lines = open(path).read().splitlines()
    for s in settings:
        name, value = s.split("=", 1)
        key = "CONFIG_" + name
        new = "# %s is not set" % key if value == "n" else "%s=%s" % (key, value)
        rx = re.compile(r"^(# )?%s(=| is not set)" % re.escape(key))
        for i, line in enumerate(lines):
            if rx.match(line):
                lines[i] = new
                break
        else:
            lines.append(new)
    open(path, "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
