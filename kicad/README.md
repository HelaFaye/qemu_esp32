# Boards from KiCad

`kiboard.py` reads a KiCad design and writes the QEMU board for it: the ESP32-S3, the
parts this emulation has models for, and how they're wired. The design is either the
board file (`.kicad_pcb`: every pad carries its net and, when it came from a schematic,
its pin's name) or a netlist (`kicad-cli sch export netlist`).

```sh
kicad/kiboard.py my-board.kicad_pcb               # what it found, and the QEMU arguments
kicad/kiboard.py my-board.net --python board.py   # a function for firmware/boards.py
```

```python
node = boot("kicad:my-board.kicad_pcb", "flash.bin", "run", sd="card.img")
```

What it does:
- **The chip.** An ESP32-S3 (module or chip) by its value, symbol or footprint; its GPIOs
  by pin name (`IO10`, `GPIO10`, `RXD0`). A WROOM-1 placed without a schematic: by pad,
  from Espressif's pin table. Other ESP32s are reported (the C3 and classic ESP32 have
  QEMU machines but not these board models; the C5, C6, H2, P4 none).
- **Parts.** The displays (ILI9341, ILI9488, ST7789, ST7735, ST7796, GC9A01), touch
  (XPT2046, GT911, CST816, FT6336), SX1262/1261/1268 and modules built on them (E22,
  RA-01S, ...), SD sockets, MCP23017, SSD1306/SH1106, and push buttons to ground. Their
  pins matched by name; a part with no pin names, by its datasheet pinout (MCP23017,
  XPT2046, microSD); a display module on a plain header, by its nets' names (`LCD_CS`,
  `TFT_DC`).
- **Buses.** Parts sharing a clock (SPI) or SDA (I2C) net share a bus, each bus the next
  of the S3's (SPI2, SPI3; I2C0, I2C1). An MCP23017's address from where its A0-A2 go.
- **Lines.** Chip selects, D/C, backlight, BUSY, DIO1, interrupts: linked to the GPIO their
  net reaches, open-drain with a pull-up where the part's line is open-drain.
- **What it can't do, it says.** A chip select or reset that reaches no GPIO, an I2C bus
  without pull-ups, an active-high button, a third SPI bus, parts it doesn't know.

`fixtures/make_carrier.py` builds a test design with KiCad's own `pcbnew` API (gbagate's
touch carrier, with and without pin names); `test_kiboard.py` checks kiboard reads it
back to the hand-written board in `firmware/boards.py`, and that QEMU starts with it.

Next (docs/stress-testing.md, 5): the routing (trace lengths, nets running side by side,
distance to the antenna) into bus-noise rates per net, and a KiCad action plugin.
