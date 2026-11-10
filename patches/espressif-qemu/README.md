# Patches to Espressif's QEMU

Peripheral models for the ESP32-S3, so firmware for real boards (displays, touch, SD
cards, buttons, links) runs under QEMU with its own drivers, and tests can check what it
does on its pins and buses. They apply to Espressif's QEMU (`esp-develop`) at the commit
in `BASE`; `build.sh [dir]` fetches it, applies them and builds `qemu-system-xtensa`.

| Patch | What |
|---|---|
| 0001 | GPIO pins as wires: levels from GPIO_OUT or peripheral signals (GPIO matrix), external drives, IO_MUX pulls; IN reads them; edge and level interrupts to the CPU |
| 0002 | GP-SPI2/3 masters (W buffer and GDMA, all phases, chip selects, timing at the configured clock); `esp32s3.pin-link` to wire boards from the command line |

## Wiring a board

A device goes on a bus with `-device`, and `esp32s3.pin-link` connects its lines to pins:

```
qemu-system-xtensa -machine esp32s3 -drive file=flash.bin,if=mtd,format=raw \
  -device gd25q32,bus=spi2,id=flash \
  -device esp32s3.pin-link,pin=10,dev=flash,line=ssi-gpio-cs,pull=up
```

`pin-link` properties: `pin`; `dev` (an `id`, or a QOM path); `line` (a named GPIO line,
empty for unnamed) and `n`; `dir=in` (the device follows the pin, the default) or `dir=out`
(the device drives the pin); `open-drain=on` (a 1 releases the pin); `pull=up|down` (a
resistor on the board, also on its own without `dev`); `invert=on`.

A device on an SPI bus is selected by its CS pin, so it works whether the firmware uses
the controller's chip selects (through the GPIO matrix, or the IO_MUX on GPIO9-14) or
toggles a GPIO itself.

## Tests

`tests/qtest/esp32s3-gpio-test.c` and `esp32s3-gpspi-test.c`; CI runs them with
Espressif's own ESP qtests (`.github/workflows/espressif-qemu.yml`).
