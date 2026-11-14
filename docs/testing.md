# Testing

Three layers, from the registers up.

## Device tests (qtests)

Each patch comes with QEMU qtests that drive its devices the way their drivers do:
register by register, checking pins, buses, interrupts and timing. They live in the
patched tree, `tests/qtest/`:

| Test | Covers |
|---|---|
| `esp32s3-intmatrix-test` | The interrupt matrix's status registers |
| `esp32s3-gpio-test` | Pins, the GPIO matrix, IO_MUX pulls, interrupts |
| `esp32s3-gpspi-test` | GP-SPI2/3: chip selects, phases, timing, GDMA |
| `esp32s3-spi-devices-test` | ILI9341, XPT2046, SD; a display's power domain; noise on its wires |
| `esp32s3-i2c-test` | I2C0/1: transfers, NACKs, long writes |
| `esp32s3-i2c-devices-test` | SSD1306, touch, keyboard, MCP23017, buttons and encoders; power domains; bus noise; pin glitches |
| `esp32s3-sx1262-test` | Two QEMUs' radios on one air |
| `esp32s3-usb-serial-jtag-test` | USB Serial/JTAG with and without a host |
| `esp32s3-brownout-test` | The supply, the brown-out detector, power cuts |

```sh
cd ~/esp-qemu/build
for t in esp32s3-intmatrix-test esp32s3-gpio-test esp32s3-gpspi-test esp32s3-spi-devices-test \
         esp32s3-i2c-test esp32s3-i2c-devices-test esp32s3-sx1262-test \
         esp32s3-usb-serial-jtag-test esp32s3-brownout-test; do
  ninja tests/qtest/$t && QTEST_QEMU_BINARY=./qemu-system-xtensa ./tests/qtest/$t
done
```

The Python tools have their own: `python3 -m pytest radio/ faults/ kicad/`.

## Real firmware as test cases

Each emulated feature is confirmed by real firmware, built from its own repository at a
pinned commit and checked on its board by what it does: what it prints, what's on its
screen, what it sends. A failure means the emulation is wrong or missing something, and
the check says where. These projects aren't maintained here; they were picked for the
peripherals they use. Details and how to run each: [`firmware/`](../firmware/README.md).

| Firmware | Board | What it confirms |
|---|---|---|
| [gbagate](https://github.com/HelaFaye/gbagate) | its touch carrier and devkit | ILI9341, XPT2046, SD over SPI with ESP-IDF's sdspi, MCP23017s, ST7735; the picture read back from the glass, touches reaching the UI |
| [meshcore-bare-esp32s3](https://github.com/mytechnotalent/meshcore-bare-esp32s3) | ESP32-S3 + SX1262 | GP-SPI2, GPIO interrupts, the SX1262 and time on air, the air hub; NimBLE over virtual Bluetooth with a phone pairing. Two nodes advertise over LoRa and store each other as contacts |
| [LilyGO T-Deck](https://github.com/Xinyuan-LilyGO/T-Deck) (factory firmware) | T-Deck | The ST7789 under TFT_eSPI and LVGL, the GT911 and keyboard, RadioLib on the SX1262, PSRAM, USB Serial/JTAG, its peripherals' power switch. Known gap: Wi-Fi start-up stops in the PHY's calibration, so its main loop doesn't run yet |

And the stress demos, each against a small target firmware of its own
([`faults/`](../faults/README.md)): a glitch campaign on a PIN check, power cuts during
NVS writes, ESP-IDF's I2C driver through bus noise.

## CI

| Workflow | Runs |
|---|---|
| `.github/workflows/espressif-qemu.yml` | Builds the patched QEMU, runs the qtests above and Espressif's own, and the Python tests |
| `.github/workflows/firmware.yml` | Builds QEMU once, then each test case's firmware from its repository, and checks it on its board; the fault, power and bus demos; boards from KiCad |
| `.github/workflows/main.yml` | Ebiroll's original build of `examples/` (upstream; failing, not maintained here) |

Logs, screenshots and campaign results are uploaded as artifacts of each run.
