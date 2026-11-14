#!/usr/bin/env python3
"""Builds carrier-touch.kicad_pcb with KiCad's own pcbnew API: gbagate's touch carrier
(firmware/boards.py gbagate_carrier_touch) as a real board file, with an ESP32-S3-WROOM-1
for its Supermini. kicad/test_kiboard.py checks kiboard reads it back to that board.

    /usr/bin/python3 kicad/fixtures/make_carrier.py [--no-pin-names] OUT.kicad_pcb

Needs KiCad (7 or later) and its footprint library.
"""
import os
import sys

import pcbnew

FP = os.environ.get("KICAD_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
names = "--no-pin-names" not in sys.argv
out = [a for a in sys.argv[1:] if not a.startswith("--")][0]

board = pcbnew.BOARD()
nets = {}


def net(name):
    if name not in nets:
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
        nets[name] = n
    return nets[name]


def part(lib, fp, ref, value, x, y, pads):
    """pads: pad number -> (pin name or None, net)."""
    f = pcbnew.FootprintLoad(os.path.join(FP, lib + ".pretty"), fp)
    f.SetReference(ref)
    f.SetValue(value)
    f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    board.Add(f)
    for pad in f.Pads():
        num = pad.GetNumber()
        if num in pads:
            pin, n = pads[num]
            pad.SetNet(net(n))
            if pin and names:
                pad.SetPinFunction(pin)
    return f


# The S3 module: the GPIOs the carrier uses (WROOM-1 pads per Espressif's datasheet)
gpio_pads = {"1": ("GND", "GND"), "2": ("3V3", "+3V3"), "40": ("GND", "GND"),
             "20": ("IO12", "SCK"), "19": ("IO11", "MOSI"), "21": ("IO13", "MISO"),
             "18": ("IO10", "LCD_CS"), "17": ("IO9", "LCD_DC"), "9": ("IO16", "TOUCH_CS"),
             "10": ("IO17", "TOUCH_IRQ"), "8": ("IO15", "SD_CS"), "39": ("IO1", "SDA"),
             "38": ("IO2", "SCL"), "27": ("IO0", "BOOT"), "36": ("RXD0", "RX0"), "37": ("TXD0", "TX0")}
part("RF_Module", "ESP32-S3-WROOM-1", "U1", "ESP32-S3-WROOM-1", 100, 100, gpio_pads)

# The display: an ILI9341 module on a 14-pin header (its pins have no names: its nets do)
hdr = {"1": (None, "+3V3"), "2": (None, "GND"), "3": (None, "LCD_CS"), "4": (None, "LCD_RST"),
       "5": (None, "LCD_DC"), "6": (None, "MOSI"), "7": (None, "SCK"), "8": (None, "+3V3"),
       "9": (None, "MISO")}
part("Connector_PinHeader_2.54mm", "PinHeader_1x14_P2.54mm_Vertical", "J1", "ILI9341 2.8in module",
     140, 80, hdr)

# Touch: an XPT2046 on the same bus
part("Package_SO", "TSSOP-16_4.4x5mm_P0.65mm", "U2", "XPT2046", 140, 120,
     {"1": ("VCC", "+3V3"), "6": ("GND", "GND"), "10": ("VCC", "+3V3"), "11": ("~{PENIRQ}", "TOUCH_IRQ"),
      "12": ("DOUT", "MISO"), "14": ("DIN", "MOSI"), "15": ("~{CS}", "TOUCH_CS"), "16": ("DCLK", "SCK")})
part("Resistor_SMD", "R_0603_1608Metric", "R3", "10k", 135, 125, {"1": ("~", "TOUCH_IRQ"), "2": ("~", "+3V3")})

# microSD
part("Connector_Card", "microSD_HC_Hirose_DM3AT-SF-PEJM5", "J2", "Micro_SD_Card", 160, 100,
     {"2": ("DAT3/CD", "SD_CS"), "3": ("CMD", "MOSI"), "4": ("VDD", "+3V3"), "5": ("CLK", "SCK"),
      "6": ("VSS", "GND"), "7": ("DAT0", "MISO")})

# Two MCP23017s on I2C0: 0x20 and (A0 high) 0x21; SDA/SCL pulled up
for ref, a0, x in (("U3", "GND", 60), ("U4", "+3V3", 80)):
    part("Package_SO", "SSOP-28_5.3x10.2mm_P0.65mm", ref, "MCP23017_SS", x, 140,
         {"9": ("VDD", "+3V3"), "10": ("VSS", "GND"), "12": ("SCK", "SCL"), "13": ("SDA", "SDA"),
          "15": ("A0", a0), "16": ("A1", "GND"), "17": ("A2", "GND"), "18": ("~{RESET}", "+3V3")})
part("Resistor_SMD", "R_0603_1608Metric", "R1", "4.7k", 70, 125, {"1": ("~", "SDA"), "2": ("~", "+3V3")})
part("Resistor_SMD", "R_0603_1608Metric", "R2", "4.7k", 72, 125, {"1": ("~", "SCL"), "2": ("~", "+3V3")})

# BOOT
part("Button_Switch_SMD", "SW_SPST_PTS645", "SW1", "SW_Push", 90, 70, {"1": ("1", "BOOT"), "2": ("2", "GND")})

# A trace, so the file has routing (for kiboard's later use of it)
t = pcbnew.PCB_TRACK(board)
t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(100), pcbnew.FromMM(90)))
t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(140), pcbnew.FromMM(80)))
t.SetWidth(pcbnew.FromMM(0.2))
t.SetLayer(pcbnew.F_Cu)
t.SetNet(net("SCK"))
board.Add(t)

board.Save(out)
print("wrote", out)
