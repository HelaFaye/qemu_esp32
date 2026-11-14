#!/usr/bin/env python3
"""A QEMU board from a KiCad design: the ESP32-S3, the parts this emulation has models
for, and how they're wired, read from the design instead of written by hand.

    kicad/kiboard.py design.kicad_pcb            # or a netlist: kicad-cli sch export netlist
    kicad/kiboard.py design.net --json board.json --python board.py

From a .kicad_pcb every pad carries its net and (when the board came from a schematic)
its pin's name; a netlist has the same. Parts are recognized by their value, symbol or
footprint, then matched pin by pin by name (a display module on a plain connector: by
its nets' names). What isn't recognized, or doesn't add up (a chip select that reaches
no GPIO), is reported, never guessed.

The result is the same thing a hand-written board in firmware/boards.py returns: the
machine and its -device arguments. boards.boot() takes a design directly:
    boot("kicad:design.kicad_pcb", flash, workdir)
"""
import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------------------
# S-expressions (KiCad's file format)


def parse_sexpr(text):
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', text)
    stack, cur = [], []
    for t in tokens:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        elif t.startswith('"'):
            cur.append(t[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            cur.append(t)
    return cur[0] if cur else []


def children(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def child(node, name):
    c = children(node, name)
    return c[0] if c else None


def value(node, name, default=None):
    c = child(node, name)
    return c[1] if c and len(c) > 1 else default


# ---------------------------------------------------------------------------------------
# The design as parts and nets


class Part:
    def __init__(self, ref, value, footprint, lib):
        self.ref, self.value, self.footprint, self.lib = ref, value or "", footprint or "", lib or ""
        self.pins = {}          # pad number -> (pin name, net name)

    def text(self):
        return " ".join((self.value, self.lib, self.footprint))

    def net_of(self, *names):
        """The net of the first pin whose name (or number) matches one of the regexes."""
        for rx in names:
            for num, (pname, net) in sorted(self.pins.items()):
                if re.fullmatch(rx, pname or "", re.I) or re.fullmatch(rx, num, re.I):
                    return net
        return None

    def __repr__(self):
        return "%s (%s)" % (self.ref, self.value)


def clean_net(name):
    if not name:
        return None
    name = name.split("/")[-1] if not name.startswith("Net-(") else name
    return name or None


def load(path):
    """Parts from a .kicad_pcb or a KiCad netlist (.net)."""
    tree = parse_sexpr(open(path, encoding="utf-8", errors="replace").read())
    parts = []
    if tree and tree[0] == "kicad_pcb":
        for fp in children(tree, "footprint") + children(tree, "module"):
            props = {c[1]: c[2] for c in children(fp, "property") if len(c) > 2}
            texts = {c[1]: c[2] for c in children(fp, "fp_text") if len(c) > 2}
            ref = props.get("Reference") or texts.get("reference")
            val = props.get("Value") or texts.get("value")
            p = Part(ref, val, fp[1], "")
            for pad in children(fp, "pad"):
                net = child(pad, "net")
                p.pins[pad[1]] = (value(pad, "pinfunction"), clean_net(net[2]) if net and len(net) > 2 else None)
            parts.append(p)
    elif tree and tree[0] == "export":
        comps = {}
        for c in children(child(tree, "components") or [], "comp"):
            lib = child(c, "libsource")
            comps[value(c, "ref")] = Part(value(c, "ref"), value(c, "value"), value(c, "footprint"),
                                          "%s:%s" % (value(lib, "lib", ""), value(lib, "part", "")) if lib else "")
        for n in children(child(tree, "nets") or [], "net"):
            name = clean_net(value(n, "name"))
            for node in children(n, "node"):
                p = comps.get(value(node, "ref"))
                if p:
                    p.pins[value(node, "pin")] = (value(node, "pinfunction"), name)
        parts = list(comps.values())
    else:
        raise SystemExit("%s: not a .kicad_pcb or a KiCad netlist" % path)
    return parts


# ---------------------------------------------------------------------------------------
# The chip

# ESP32-S3-WROOM-1/-1U pads to GPIOs (Espressif's datasheet, pin definitions), for a
# board laid out without a schematic (pads then carry no pin names)
WROOM1_PADS = {4: 4, 5: 5, 6: 6, 7: 7, 8: 15, 9: 16, 10: 17, 11: 18, 12: 8, 13: 19, 14: 20,
               15: 3, 16: 46, 17: 9, 18: 10, 19: 11, 20: 12, 21: 13, 22: 14, 23: 21, 24: 47,
               25: 48, 26: 45, 27: 0, 28: 35, 29: 36, 30: 37, 31: 38, 32: 39, 33: 40, 34: 41,
               35: 42, 36: 44, 37: 43, 38: 2, 39: 1}
CHIPS = [
    (r"ESP32-?S3", "esp32s3"),
    (r"ESP32-?C3", "esp32c3"),
    (r"ESP32-?C5|ESP32-?C6|ESP32-?H2|ESP32-?P4|ESP32-?S2|ESP32-?C2|ESP32-?C61", None),
    (r"ESP32(?!-?[SCHP]\d)", "esp32"),
]


def find_chip(parts):
    for p in parts:
        for rx, machine in CHIPS:
            if re.search(rx, p.value + " " + p.lib + " " + p.footprint, re.I):
                if re.search(r"WROOM|WROVER|MINI|PICO|ESP32", p.text(), re.I) and len(p.pins) > 10:
                    return p, machine
    return None, None


def gpio_map(chip):
    """net -> GPIO number."""
    nets = {}
    named = False
    for num, (pname, net) in chip.pins.items():
        m = re.fullmatch(r"(?:GPIO|IO)(\d+)(?:[/_].*)?|.*\b(?:GPIO|IO)(\d+)\b.*", pname or "", re.I)
        if m:
            named = True
            g = int(m.group(1) or m.group(2))
        elif re.fullmatch(r"U?0?RXD0?|RXD0", pname or "", re.I):
            named, g = True, 44
        elif re.fullmatch(r"U?0?TXD0?|TXD0", pname or "", re.I):
            named, g = True, 43
        else:
            continue
        if net:
            nets.setdefault(net, g)
    if not named and re.search(r"WROOM-?1", chip.text(), re.I):
        for num, (_, net) in chip.pins.items():
            if num.isdigit() and int(num) in WROOM1_PADS and net:
                nets.setdefault(net, WROOM1_PADS[int(num)])
    return nets


# ---------------------------------------------------------------------------------------
# Parts this emulation has (patches/espressif-qemu), and how to know them

GND = re.compile(r"^(GND|VSS|0V|AGND|DGND|GNDA|GNDD|Earth)$", re.I)
POWER = re.compile(r"^\+?(3V3|3\.3V|VCC|VDD|3V|VDD_?3V3|\+3V3|5V|VBUS|VIN|VSYS)$", re.I)

# kind: (match on value/lib/footprint, bus, qemu device, roles: role -> pin-name regexes)
SPI_ROLES = {"sck": [r"SCK|SCLK|CLK|SCL|DCLK|SPI_?CLK"], "mosi": [r"MOSI|SDI|DIN|SDA|DI|SI|COPI"],
             "miso": [r"MISO|SDO|DOUT|DO|SO|CIPO"], "cs": [r"N?CSX?|N?SS|NSS|CS_?N|~?\{?CS\}?|SEL"]}
KINDS = [
    ("ili9341", r"ILI9341", "spi", {"dc": [r"D/?C|DCX|RS|A0"], "reset": [r"N?RE?SE?T|RESX|RST"],
                                     "backlight": [r"BL|LED|BLK|LEDA?"]}),
    ("ili9488", r"ILI9488", "spi", {"dc": [r"D/?C|DCX|RS"], "reset": [r"N?RE?SE?T|RST"], "backlight": [r"BL|LED|BLK"]}),
    ("st7789", r"ST7789", "spi", {"dc": [r"D/?C|DCX|RS"], "reset": [r"N?RE?SE?T|RST"], "backlight": [r"BL|LED|BLK"]}),
    ("st7735", r"ST7735", "spi", {"dc": [r"D/?C|DCX|RS|A0"], "reset": [r"N?RE?SE?T|RST"], "backlight": [r"BL|LED|BLK"]}),
    ("st7796", r"ST7796", "spi", {"dc": [r"D/?C|DCX|RS"], "reset": [r"N?RE?SE?T|RST"], "backlight": [r"BL|LED|BLK"]}),
    ("gc9a01", r"GC9A01", "spi", {"dc": [r"D/?C|DCX|RS"], "reset": [r"N?RE?SE?T|RST"], "backlight": [r"BL|LED|BLK"]}),
    ("xpt2046", r"XPT2046|ADS7843|ADS7846|TSC2046", "spi", {"penirq": [r"~?\{?PENIRQ\}?|PEN|T_?IRQ|IRQ"]}),
    ("sx1262", r"SX126[128]|E22-\d+M\d+S|RA-?01S|HT-?RA62|LLCC68|Wio-?SX1262|CORE1262", "spi",
     {"reset": [r"N?RE?SE?T|NRST|RST"], "busy": [r"BUSY"], "dio1": [r"DIO1|IRQ"]}),
    ("ssi-sd", r"micro_?SD|TF_?card|SD_?card|Micro_SD|SD_Card|MicroSD|DM3AT|TF-?01", "spi",
     {"cs": [r"DAT3|CD/DAT3|DAT3/CD|CS|D3"], "mosi": [r"CMD|DI|MOSI"], "miso": [r"DAT0|DO|MISO|D0"],
      "sck": [r"CLK|SCK|SCLK"]}),
    ("mcp23017", r"MCP23017", "i2c", {"inta": [r"INTA"], "intb": [r"INTB"]}),
    ("gt911", r"GT911", "i2c", {"int": [r"INT|IRQ"], "reset": [r"N?RE?SE?T|RSTN?"]}),
    ("cst816", r"CST816", "i2c", {"int": [r"INT|IRQ"], "reset": [r"N?RE?SE?T|RST"]}),
    ("ft6336", r"FT6336|FT6206|FT6236", "i2c", {"int": [r"INT|IRQ"], "reset": [r"N?RE?SE?T|RST"]}),
    ("ssd1306", r"SSD1306", "i2c", {}),
    ("sh1106", r"SH1106", "i2c", {}),
]
I2C_ROLES = {"sda": [r"SDA|SDI|DATA"], "scl": [r"SCL|SCK|CLK"]}
# Pin names by pad number, from datasheets, for parts placed without a schematic (the
# same numbering in every package these parts come in)
PINOUTS = {
    "mcp23017": {"9": "VDD", "10": "VSS", "12": "SCK", "13": "SDA", "15": "A0", "16": "A1", "17": "A2",
                 "18": "~{RESET}", "19": "INTB", "20": "INTA"},
    "xpt2046": {"1": "VCC", "6": "GND", "10": "VCC", "11": "~{PENIRQ}", "12": "DOUT", "13": "BUSY",
                "14": "DIN", "15": "~{CS}", "16": "DCLK"},
    "ssi-sd": {"1": "DAT2", "2": "DAT3/CD", "3": "CMD", "4": "VDD", "5": "CLK", "6": "VSS", "7": "DAT0",
               "8": "DAT1"},
}
I2C_ADDR = {"mcp23017": 0x20, "gt911": 0x5D, "cst816": 0x15, "ft6336": 0x38, "ssd1306": 0x3C, "sh1106": 0x3C}
OUTPUTS = {"busy", "dio1", "penirq", "inta", "intb", "int"}           # Driven by the part
OPEN_DRAIN = {"penirq", "inta", "intb"}
# Nets named for their role: a display or module on a plain connector
NET_ROLES = {"cs": r"(LCD|TFT|DISP|SD|TF|LORA|TOUCH|T)?_?N?CS\d?|.*_CS|CS_.*|.*_SS|NSS",
             "dc": r"(LCD|TFT|DISP)?_?(DC|D/C|RS|A0)|.*_DC", "reset": r"(LCD|TFT|DISP|LORA)?_?(RST|RESET)|.*_RST|.*_RESET",
             "backlight": r"(LCD|TFT|DISP)?_?(BL|BLK|LED|BACKLIGHT)|.*_BL", "sck": r".*(SCK|SCLK|CLK)",
             "mosi": r".*(MOSI|SDI)", "miso": r".*(MISO|SDO)", "busy": r".*BUSY", "dio1": r".*DIO1",
             "penirq": r".*(PENIRQ|IRQ)", "int": r".*_?INT", "sda": r".*SDA.*", "scl": r".*SCL.*"}


def roles_by_pins(part, wanted):
    out = {}
    for role, rxs in wanted.items():
        net = part.net_of(*rxs)
        if net:
            out[role] = net
    return out


def roles_by_nets(part, roles):
    """For a part on a connector: its nets' names say what they are."""
    out = {}
    nets = [n for _, n in part.pins.values() if n]
    for role in roles:
        for n in nets:
            if re.fullmatch(NET_ROLES.get(role, r"$^"), n, re.I) and n not in out.values():
                out[role] = n
                break
    return out


class Board:
    def __init__(self, parts):
        self.parts = parts
        self.chip, self.machine = find_chip(parts)
        self.gpio = gpio_map(self.chip) if self.chip else {}
        self.devices = []       # (kind, part, bus, info)
        self.args = []
        self.notes = []
        self.unknown = []

    def pin(self, net):
        return self.gpio.get(net)

    def level(self, net):
        if net is None:
            return None
        if GND.match(net):
            return 0
        if POWER.match(net):
            return 1
        return None

    def pullups(self):
        """GPIO nets with a resistor to a supply: pulled up on the board."""
        ups = set()
        for p in self.parts:
            if re.match(r"^R\d", p.ref or "") and len(p.pins) == 2:
                a, b = [n for _, n in p.pins.values()]
                for x, y in ((a, b), (b, a)):
                    if x in self.gpio and y and POWER.match(y):
                        ups.add(x)
        return ups

    def build(self):
        if not self.chip:
            self.notes.append("no ESP32 found: nothing to emulate")
            return self
        if self.machine is None:
            self.notes.append("%s is an %s: QEMU has no model of it; parts below are listed, not emulated"
                              % (self.chip.ref, self.chip.value))
        elif self.machine != "esp32s3":
            self.notes.append("%s is an %s: these board models (pins, SPI, I2C) are the S3's"
                              % (self.chip.ref, self.chip.value))
        spi_buses, i2c_buses = {}, {}
        ups = self.pullups()
        for p in self.parts:
            if p is self.chip:
                continue
            kind = next((k for k in KINDS if re.search(k[1], p.text(), re.I)), None)
            if kind:
                self.add(p, kind, spi_buses, i2c_buses, ups)
            elif re.search(r"SW_Push|SW_Tactile|Button|SW_SPST", p.text(), re.I):
                self.add_button(p)
            elif not re.match(r"^(R|C|L|D|FB|TP|H|MH|J|JP|LED|Q|F|Y|X|BT)\d", p.ref or ""):
                self.unknown.append(p)
        return self

    def add(self, p, kind, spi_buses, i2c_buses, ups):
        name, _, bus, extra = kind
        ident = re.sub(r"\W", "_", p.ref.lower())
        if name in PINOUTS and not any(n for n, _ in p.pins.values()):
            p.pins = {num: (PINOUTS[name].get(num), net) for num, (_, net) in p.pins.items()}
            self.notes.append("%s: no pin names in the design; %s's datasheet pinout used" % (p.ref, name))
        roles = roles_by_pins(p, {**(SPI_ROLES if bus == "spi" else I2C_ROLES), **extra})
        connector = sum(1 for n, _ in p.pins.values() if n) < 3       # Pins without names
        if connector or len(roles) < 3:
            roles.update({k: v for k, v in roles_by_nets(p, list((SPI_ROLES if bus == "spi" else I2C_ROLES)) + list(extra)).items()
                          if k not in roles})
        if bus == "spi":
            sck = roles.get("sck")
            if not sck or self.pin(sck) is None:
                self.notes.append("%s (%s): its SPI clock doesn't reach a GPIO; left out" % (p.ref, name))
                return
            if sck not in spi_buses:
                if len(spi_buses) == 2:
                    self.notes.append("%s: a third SPI bus; the S3 has two for these (SPI2, SPI3); left out" % p.ref)
                    return
                spi_buses[sck] = {"bus": "spi%d" % (2 + len(spi_buses)), "cs": 0}
            b = spi_buses[sck]
            cs = roles.get("cs")
            dev = "%s,bus=%s,cs=%d,id=%s" % (name, b["bus"], b["cs"], ident)
            b["cs"] += 1
            self.args += ["-device", dev]
            if cs is not None and self.pin(cs) is not None:
                self.link(self.pin(cs), ident, "ssi-gpio-cs", pull="up")
            else:
                self.notes.append("%s (%s): its chip select doesn't reach a GPIO" % (p.ref, name))
            self.devices.append((name, p, b["bus"], roles))
        else:
            sda = roles.get("sda")
            if not sda or self.pin(sda) is None:
                self.notes.append("%s (%s): its SDA doesn't reach a GPIO; left out" % (p.ref, name))
                return
            if sda not in i2c_buses:
                if len(i2c_buses) == 2:
                    self.notes.append("%s: a third I2C bus; the S3 has two; left out" % p.ref)
                    return
                i2c_buses[sda] = "i2c%d" % len(i2c_buses)
            addr = I2C_ADDR[name]
            if name == "mcp23017":
                for i, a in enumerate(("A0", "A1", "A2")):
                    lv = self.level(p.net_of(a))
                    if lv is None:
                        self.notes.append("%s: %s isn't tied to GND or a supply; taken as 0" % (p.ref, a))
                    addr |= (lv or 0) << i
            elif name in ("ssd1306", "sh1106"):
                lv = self.level(p.net_of(r"SA0|D/C|DC|A0"))
                addr |= lv or 0
            elif name == "gt911":
                self.notes.append("%s: GT911 at 0x5D (its INT level at reset picks 0x5D or 0x14)" % p.ref)
            if sda not in ups:
                self.notes.append("%s: no pull-up on SDA (%s) on the board" % (p.ref, sda))
            self.args += ["-device", "%s,bus=%s,address=0x%02x,id=%s" % (name, i2c_buses[sda], addr, ident)]
            self.devices.append((name, p, i2c_buses[sda], roles))
        for role, net in roles.items():
            if role in ("sck", "mosi", "miso", "cs", "sda", "scl"):
                continue
            g = self.pin(net)
            if g is None:
                lv = self.level(net)
                if lv is None:
                    self.notes.append("%s %s: on %s, which reaches no GPIO" % (p.ref, role, net))
                continue
            line = {"reset": "reset", "backlight": "backlight", "dc": "dc", "busy": "busy", "dio1": "dio1",
                    "penirq": "penirq", "inta": "inta", "intb": "intb", "int": "int"}[role]
            if line == "reset" and name in ("ili9341", "ili9488", "st7789", "st7735", "st7796", "gc9a01", "gt911",
                                            "cst816", "ft6336"):
                continue            # These models have no reset line; their reset is the command
            self.link(g, ident, line, out=role in OUTPUTS, open_drain=role in OPEN_DRAIN,
                      pull="up" if role in OPEN_DRAIN or net in ups else None)

    def add_button(self, p):
        nets = [n for _, n in p.pins.values() if n]
        sig = [n for n in nets if self.pin(n) is not None]
        other = [n for n in nets if n not in sig]
        if len(sig) != 1:
            self.notes.append("%s: a switch not between one GPIO and a rail; left out" % p.ref)
            return
        if other and self.level(other[0]) == 1:
            self.notes.append("%s: an active-high button (to %s); gpio-button pulls low; left out"
                              % (p.ref, other[0]))
            return
        ident = re.sub(r"\W", "_", p.ref.lower())
        self.args += ["-device", "gpio-button,pin=%d,id=%s" % (self.pin(sig[0]), ident)]
        self.devices.append(("gpio-button", p, None, {"pin": sig[0]}))

    def link(self, pin, dev, line, out=False, open_drain=False, pull=None):
        a = "esp32s3.pin-link,pin=%d,dev=%s,line=%s" % (pin, dev, line)
        if out:
            a += ",dir=out"
        if open_drain:
            a += ",open-drain=on"
        if pull:
            a += ",pull=" + pull
        self.args += ["-device", a]

    def report(self):
        lines = []
        if self.chip:
            lines.append("chip: %s %s -> %s, %d nets on GPIOs" % (self.chip.ref, self.chip.value,
                                                                   self.machine or "not emulated", len(self.gpio)))
        for kind, p, bus, roles in self.devices:
            wiring = ", ".join("%s %s%s" % (r, n, "" if self.pin(n) is None else " (GPIO%d)" % self.pin(n))
                               for r, n in sorted(roles.items()))
            lines.append("  %-6s %-12s %-6s %s" % (p.ref, kind, bus or "", wiring))
        for p in self.unknown:
            lines.append("  %-6s not emulated: %s" % (p.ref, p.value))
        for n in self.notes:
            lines.append("note: " + n)
        return "\n".join(lines)

    def python(self, fn="board"):
        out = ["def %s(**_):" % fn,
               '    """Generated by kicad/kiboard.py from the KiCad design."""',
               "    return %r, [" % (self.machine or "esp32s3")]
        for i in range(0, len(self.args), 2):
            out.append("        %r, %r," % (self.args[i], self.args[i + 1]))
        out.append("    ]")
        return "\n".join(out) + "\n"


def board_from(path):
    return Board(load(path)).build()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("design", help=".kicad_pcb or a KiCad netlist (.net)")
    ap.add_argument("--json")
    ap.add_argument("--python", help="write a boards.py-style function")
    a = ap.parse_args()
    b = board_from(a.design)
    print(b.report())
    print("\nQEMU: -machine %s %s" % (b.machine or "?", " ".join(b.args)))
    if a.json:
        json.dump({"machine": b.machine, "args": b.args, "notes": b.notes,
                   "unknown": [[p.ref, p.value] for p in b.unknown]}, open(a.json, "w"), indent=1)
    if a.python:
        open(a.python, "w").write(b.python(re.sub(r"\W", "_", os.path.splitext(os.path.basename(a.design))[0])))
    return 0 if b.machine else 1


if __name__ == "__main__":
    sys.exit(main())
