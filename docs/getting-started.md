# Getting started

## Build QEMU

```sh
patches/espressif-qemu/build.sh ~/esp-qemu
export QEMU_XTENSA=~/esp-qemu/build/qemu-system-xtensa
```

`build.sh` fetches [Espressif's QEMU](https://github.com/espressif/qemu) at the commit in
`patches/espressif-qemu/BASE`, applies the patch series and builds
`qemu-system-xtensa`. On Ubuntu it needs `ninja-build libglib2.0-dev libpixman-1-dev
libgcrypt20-dev libslirp-dev flex bison`. If the ROM files aren't found, point
`QEMU_BIOS` at `~/esp-qemu/pc-bios`.

## A flash image

QEMU takes the whole flash chip as one file: bootloader, partition table and app merged,
padded to the chip's size (4, 8 or 16 MB):

```sh
esptool --chip esp32s3 merge-bin --pad-to-size 16MB -o flash.bin \
    0x0 bootloader.bin 0x8000 partitions.bin 0x10000 firmware.bin
```

(`boot()` below pads a smaller image itself.) ESP-IDF, Arduino and PlatformIO builds all
work; the firmware needs no change.

## Run it on a board

From Python, with a board from `firmware/boards.py`:

```python
import sys; sys.path.insert(0, "firmware")
from boards import boot

node = boot("gbagate-carrier-touch", "flash.bin", "/tmp/run", card="sd.img")
m = node.wait_for(r"ready", timeout=60)       # A regex on its serial output, or None
print(node.log())
node.stop()
```

Or from the command line, wiring the parts yourself:

```sh
$QEMU_XTENSA -machine esp32s3 -display none -nic none \
  -drive file=flash.bin,if=mtd,format=raw -serial stdio \
  -device ili9341,bus=spi2,id=lcd \
  -device esp32s3.pin-link,pin=10,dev=lcd,line=ssi-gpio-cs,pull=up \
  -device esp32s3.pin-link,pin=9,dev=lcd,line=dc
```

[Boards](boards.md) covers describing a board; the devices and their lines are in the
[device reference](../patches/espressif-qemu/README.md).

## Serial ports

`-serial` options go to UART0, UART1, UART2, then (S3) the USB Serial/JTAG, which is
plugged in when it's given one. `boot()` sends UART0 to `<workdir>/<name>.log` and the
USB serial to `<name>-usb.log`; `node.log()` reads both (ESP-IDF often prints to both,
Arduino's USB CDC boards only to the second). UART1 can carry HCI to a virtual Bluetooth
controller: `boot(..., hci=PORT)`, see [`bluetooth/`](../bluetooth/README.md).

## Screens and inputs

Every display is a screen: `node.screendump(path, device="lcd")` writes it as a PPM and
returns `(width, height, rgb)`. Inputs are device properties, set over QMP:

```python
node.qmp("qom-set", path="/machine/peripheral/touch", property="x", value=1234)
node.qmp("qom-set", path="/machine/peripheral/touch", property="y", value=2048)
node.qmp("qom-set", path="/machine/peripheral/touch", property="pressed", value=True)
node.qmp("qom-set", path="/machine/peripheral/kbd", property="keys", value="hello\n")
node.qmp("qom-set", path="/machine/peripheral/ball_click", property="pressed", value=True)
```

`/machine/peripheral/<id>` is a device's `id=`. Which properties each device has: the
[device reference](../patches/espressif-qemu/README.md).

## The Python API (`firmware/boards.py`)

`boot(board, flash, workdir, name="node", hci=None, extra=(), **opts)` starts QEMU and
returns a `Node`. `board` is a name from `BOARDS` or `"kicad:<design>"`; `opts` go to the
board (`card=`, `sd=`, `air=`); `extra` adds QEMU arguments (`["-gdb", "tcp::1234"]`).
Each node gets its own copy of the flash, so its NVS is its own.

| `Node` | |
|---|---|
| `log()`, `wait_for(regex, timeout)`, `count(regex)` | Serial output (UART0 and USB) |
| `qemu_output()` | What QEMU itself printed (why it didn't start) |
| `qmp(command, **args)` | Any QMP command; its result |
| `screendump(path, device)` | A display's picture |
| `set_vdd(mv)`, `brownout(mv, s)`, `power_cut(s)` | The chip's supply ([stress testing](stress-testing.md)) |
| `part_power(domain, on)` | Parts on their own supply |
| `noise(name, ber=, nack=)`, `glitch(name, rate=, pulse=)` | Errors on board wires |
| `stop()` | Ends QEMU |

## Debugging

`boot(..., extra=["-gdb", "tcp::1234"])` (or `-s` on the command line) opens QEMU's GDB
stub. Espressif's GDB (`xtensa-esp32s3-elf-gdb firmware.elf -ex "target remote :1234"`)
can attach; `faults/inject.py` speaks the same protocol from Python (breakpoints,
registers, memory). A backtrace from a panic decodes with
`xtensa-esp32s3-elf-addr2line -pfiaC -e firmware.elf <addresses>`.

## What doesn't work yet

The S3's LCD_CAM (8080 parallel displays), ADC, LEDC, RMT and Wi-Fi (start-up gets as far
as the PHY's calibration), and its own Bluetooth controller (firmware runs its host stack
over HCI to a virtual controller instead). See [Boards](boards.md#whats-emulated).
