# Patches to Espressif's QEMU

Peripheral models for the ESP32-S3, so firmware for real boards (displays, touch, SD
cards, buttons, links) runs under QEMU with its own drivers, and tests can check what it
does on its pins and buses. They apply to Espressif's QEMU (`esp-develop`) at the commit
in `BASE`; `build.sh [dir]` fetches it, applies them and builds `qemu-system-xtensa`.

| Patch | What |
|---|---|
| 0001 | GPIO pins as wires: levels from GPIO_OUT or peripheral signals (GPIO matrix), external drives, IO_MUX pulls; IN reads them; edge and level interrupts to the CPU |
| 0002 | GP-SPI2/3 masters (W buffer and GDMA, all phases, chip selects, timing at the configured clock); `esp32s3.pin-link` to wire boards from the command line |
| 0003 | SPI displays `ili9341` `ili9488` `st7735` `st7789` `st7796` `gc9a01` (MIPI DCS; each a screen `screendump` can capture), `xpt2046` touch, `ssi-sd` SD cards with `drive=` |
| 0004 | I2C0/I2C1 masters (`i2c0`, `i2c1` buses): command lists, FIFOs, ACK checks, interrupts, SCL timing |
| 0005 | I2C board devices: `ssd1306`/`sh1106` OLEDs (screens), `gt911` `cst816` `ft6336` touch, `tdeck-keyboard`, `mcp23017`; `gpio-button` (buttons and rotary encoders on pins or expander lines) |

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

A display with touch and an SD card, as gbagate's carrier-touch board wires them:

```
  -device ili9341,bus=spi2,id=lcd \
  -device esp32s3.pin-link,pin=10,dev=lcd,line=ssi-gpio-cs,pull=up \
  -device esp32s3.pin-link,pin=9,dev=lcd,line=dc \
  -device xpt2046,bus=spi2,cs=1,id=touch \
  -device esp32s3.pin-link,pin=16,dev=touch,line=ssi-gpio-cs,pull=up \
  -device esp32s3.pin-link,pin=17,dev=touch,line=penirq,dir=out,open-drain=on,pull=up \
  -drive id=card,if=none,format=raw,file=sd.img \
  -device ssi-sd,bus=spi2,cs=2,id=sd,drive=card \
  -device esp32s3.pin-link,pin=15,dev=sd,line=ssi-gpio-cs,pull=up
```

Devices on one bus need different `cs=` numbers (QEMU's rule); which one is selected is
decided by the pins. Touch from a test: `qom-set /machine/peripheral/touch x 1234`, then
`y`, then `pressed true`. Displays: `screendump` with `device=lcd`. A panel's mounting
differs between modules: `width`/`height`/`x-offset`/`y-offset` (e.g. a 135x240 ST7789 at
52,40), `panel-mirror-x`/`-y`, `panel-bgr`, `panel-inverted`.

I2C devices go on `i2c0`/`i2c1` with their address; inputs are driven with `qom-set`:

```
  -device gt911,bus=i2c0,address=0x5d,id=touch,width=320,height=240 \
  -device esp32s3.pin-link,pin=16,dev=touch,line=int,dir=out \
  -device tdeck-keyboard,bus=i2c0,address=0x55,id=kbd \
  -device gpio-button,pin=0,id=boot,key=b
# qom-set /machine/peripheral/kbd keys "hello\n"
# qom-set /machine/peripheral/boot pressed true
# qom-set /machine/peripheral/knob turn 3          (an encoder: 3 detents)
```

## Tests

`tests/qtest/esp32s3-gpio-test.c`, `esp32s3-gpspi-test.c`, `esp32s3-spi-devices-test.c`, `esp32s3-i2c-test.c` and `esp32s3-i2c-devices-test.c`; CI runs them with
Espressif's own ESP qtests (`.github/workflows/espressif-qemu.yml`).
