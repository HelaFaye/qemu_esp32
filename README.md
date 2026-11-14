# qemu_esp32: ESP32 boards in QEMU, for firmware developers

Run your ESP32-S3 firmware, unmodified, on an emulated board: the chip's peripherals, the
parts you wire to it (displays, touch, SD cards, LoRa radios, buttons, I/O expanders,
keyboards, OLEDs) and the radio links between boards (LoRa, Bluetooth). Test drivers, UIs
and protocols before there's hardware; script what a person would do (press a button,
touch the screen, type, carry a node out of range) and check what the firmware does (its
serial output, its screen, what it sends over the air); and run it all in CI.

Then stress it the way the real world will: weak and crowded radio, noise on the wires,
brown-outs and power cuts, parts losing power, glitched instructions, with the board read
straight from your KiCad design. The aim is to find a design's weaknesses before ordering
a PCB, buying parts or wiring a breadboard.

Every model is built from the chip's register definitions and the drivers that use them
(ESP-IDF, Arduino), and tested the way those drivers drive it. Real projects' firmware
runs on it as test cases.

## Quick start

```sh
patches/espressif-qemu/build.sh ~/esp-qemu          # Espressif's QEMU with these patches
export QEMU_XTENSA=~/esp-qemu/build/qemu-system-xtensa
```

```python
import sys; sys.path.insert(0, "firmware")
from boards import boot

node = boot("t-deck", "flash.bin", "/tmp/run")       # A merged image (esptool merge-bin)
node.wait_for(r"Start Radio success", 60)            # Its serial output
node.qmp("qom-set", path="/machine/peripheral/kbd", property="keys", value="hello")
w, h, rgb = node.screendump("/tmp/run/screen.ppm")   # What its display shows
```

Or your own design: `boot("kicad:my-board.kicad_pcb", "flash.bin", "/tmp/run")`.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Building QEMU, running firmware, its serial ports, screens and inputs, debugging; the Python API |
| [Boards](docs/boards.md) | What's emulated; describing a board (in Python, on the command line, or from KiCad); the boards here |
| [Testing](docs/testing.md) | Real firmware as test cases, the device tests, CI |
| [Stress testing](docs/stress-testing.md) | Radio environments, bus noise, brown-outs, power domains, fault injection: what exists and what's planned |
| [Devices and patches](patches/espressif-qemu/README.md) | Each patch, each device, its properties and lines |

Next to the code: [`firmware/`](firmware/README.md) (the test cases),
[`radio/`](radio/README.md) (the air between LoRa radios),
[`bluetooth/`](bluetooth/README.md) (virtual Bluetooth), [`faults/`](faults/README.md)
(fault injection, power and bus errors), [`kicad/`](kicad/README.md) (boards from KiCad).

## Layout

| Where | What |
|---|---|
| `patches/espressif-qemu/` | The emulation: a patch series for [Espressif's QEMU](https://github.com/espressif/qemu) at the commit in `BASE`; `build.sh` fetches, applies and builds |
| `firmware/boards.py` | Boards as Python, and `boot()`; `firmware/<project>/` the test cases |
| `radio/` | `airhub.py` (LoRa radios in different QEMUs on one air) and `environment.py` (positions, path loss, noise, interferers) |
| `bluetooth/` | Your firmware's own Bluetooth host over HCI to a virtual controller, with virtual phones and controllers as peers |
| `faults/` | Glitches and campaigns (`inject.py`, `campaign.py`), and demos of power and bus errors |
| `kicad/` | `kiboard.py`: a QEMU board from a `.kicad_pcb` or netlist |
| `docs/upstream/`, `examples/` | Ebiroll's original qemu_esp32 (this is a fork of it): [his documentation](docs/upstream/README.md) and examples, kept as they were |
