# Boards

A board is the chip plus what's wired to it. Here it's a list of QEMU devices (each part,
on its bus) and pin links (each of the part's lines to the GPIO it's on), written in
Python, on the command line, or read from a KiCad design.

## What's emulated

| ESP32-S3 | |
|---|---|
| GPIO, GPIO matrix, IO_MUX | Pin levels from outputs, peripheral signals, external drives and pulls; edge and level interrupts |
| GP-SPI2, GP-SPI3 | Master transactions, W buffer and GDMA, chip selects through the matrix or IO_MUX, timing at the configured clock |
| I2C0, I2C1 | Command lists, FIFOs, ACK checks, interrupts, SCL timing |
| USB Serial/JTAG | The CDC serial side; start-of-frame packets while plugged in (what HWCDC checks) |
| RTC_CNTL | Its interrupts and brown-out detector, with the supply a run-time setting |
| (Espressif's) | CPUs, flash, PSRAM (`-m`), UART, GDMA, crypto, timers, SDMMC, TWAI, RGB framebuffer |

| Parts | |
|---|---|
| Displays (SPI) | `ili9341` `ili9488` `st7735` `st7789` `st7796` `gc9a01`: MIPI DCS, each a screen you can capture |
| Displays (I2C) | `ssd1306` `sh1106` |
| Touch | `xpt2046` (SPI); `gt911` `cst816` `ft6336` (I2C) |
| Storage | `ssi-sd` (an SD card over SPI, from a disk image), `at24c-eeprom` |
| Input | `gpio-button` (buttons, rotary encoders), `tdeck-keyboard`, `mcp23017` |
| Radio | `sx1262` `sx1261` `sx1268` (LoRa between QEMUs, [`radio/`](../radio/README.md)); Bluetooth through [`bluetooth/`](../bluetooth/README.md) |
| Board-level | `power-domain` (parts on a switched supply), `bus-noise` (errors on wires), `pin-glitch` (pulses on an input) |

Not yet: the S3's LCD_CAM (8080 parallel displays), ADC, LEDC/PWM, RMT, Wi-Fi, its
Bluetooth controller, SX127x radios, and the classic ESP32's and C3's peripherals beyond
Espressif's. Each device's properties and lines: the
[device reference](../patches/espressif-qemu/README.md).

## Wiring, on the command line

A part goes on a bus with `-device`; `esp32s3.pin-link` connects each of its lines to a
pin:

```sh
-device st7789,bus=spi2,cs=0,id=lcd \
-device esp32s3.pin-link,pin=12,dev=lcd,line=ssi-gpio-cs,pull=up \
-device esp32s3.pin-link,pin=11,dev=lcd,line=dc \
-device gt911,bus=i2c0,address=0x5d,id=touch,width=320,height=240 \
-device esp32s3.pin-link,pin=16,dev=touch,line=int,dir=out
```

- A part on an SPI bus is selected by its chip-select pin, so the controller's own chip
  selects and GPIO-toggled ones both work. Parts on one bus need different `cs=`
  numbers, which is all they're for.
- `dir=out`: the part drives the pin (an interrupt, BUSY); otherwise it follows the pin.
- `open-drain=on`: a 1 releases the pin (shared interrupt lines). `pull=up|down`: a
  resistor on the board (also on its own, without `dev`). `invert=on`.
- I2C parts take their 7-bit `address`.

## Boards in Python

`firmware/boards.py` has a function per board returning the machine and its arguments,
built from small helpers, from the board's schematic or pin header:

```python
def my_board(air=None, **_):
    """What it is, and where its pins come from."""
    args = ["-device", "ili9341,bus=spi2,id=lcd"]
    args += link(10, "lcd", "ssi-gpio-cs", pull="up") + link(9, "lcd", "dc")
    args += sx1262(nss=7, reset=8, busy=15, dio1=16, cs=1, air=air)
    args += button("boot", 0, key="b")
    return "esp32s3", args

BOARDS["my-board"] = my_board
```

| Helper | |
|---|---|
| `link(pin, dev, line, out=, open_drain=, pull=)` | A pin link |
| `sx1262(nss, reset, busy, dio1, bus=, cs=, air=)` | The radio and its four lines; `air` a hub port |
| `button(name, pin, key=)` | A button to ground |
| `power_domain(name, *parts, pin=)` | Parts on a switched supply, switched by a test or by a GPIO |
| `bus_noise(name, *parts, ber=, nack=, burst=, seed=)` | Bit errors and lost ACKs on parts' wires |
| `pin_glitch(name, pin, rate=, width_us=, seed=)` | Pulses pulling an input low |

The boards here, each from its project's own pin definitions:

| Board | |
|---|---|
| `esp32s3-devkitc` | The chip alone |
| `meshcore-bare` | A DevKitC and an SX1262 module |
| `t-deck` | LilyGO T-Deck: ST7789, GT911, keyboard, SX1262, SD, trackball, PSRAM; peripherals powered by GPIO10 |
| `gbagate-carrier-touch` | gbagate's touch carrier: ILI9341, XPT2046, SD, two MCP23017s |
| `gbagate-devkit` | gbagate on a DevKitC with an ST7735 |

## Boards from KiCad

`kicad/kiboard.py` reads a `.kicad_pcb` or a netlist and writes the board: the chip's
GPIOs, the parts it knows, their buses, I2C addresses from their address pins, and their
lines, with the board's pull-ups and open drains. What it can't place it reports. See
[`kicad/`](../kicad/README.md).

```python
node = boot("kicad:my-board.kicad_pcb", "flash.bin", "/tmp/run", sd="card.img")
```
