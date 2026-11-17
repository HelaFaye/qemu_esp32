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
| `esp32s3-phy-test` | What Wi-Fi start-up polls (calibrations, the MAC's reset), the temperature sensor, ADC conversions; LEDC's registers |
| `esp32s3-wifi-test` | The Wi-Fi MAC and access point: a beacon received, the receive registers ESP-IDF follows, a reload, a probe answered, nothing off channel |
| `esp32s3-rmt-test` | The RMT's transmit side: an LED's three bytes sent and read back, the threshold and end interrupts on a wrapping transmission |
| `esp32s3-mmu-test` | One flash page at two virtual pages: unmapping one leaves the other readable |
| `esp32s3-lcd-cam-test` | LCD_CAM: an ST7789 on its parallel bus, commands and pixels by GDMA, byte order, the done interrupt; the glass read back exactly |

```sh
cd ~/esp-qemu/build
for t in esp32s3-intmatrix-test esp32s3-gpio-test esp32s3-gpspi-test esp32s3-spi-devices-test \
         esp32s3-i2c-test esp32s3-i2c-devices-test esp32s3-sx1262-test \
         esp32s3-usb-serial-jtag-test esp32s3-brownout-test esp32s3-phy-test \
         esp32s3-lcd-cam-test esp32s3-wifi-test esp32s3-rmt-test esp32s3-mmu-test; do
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
| [gbagate](https://github.com/HelaFaye/gbagate) | its touch carrier, devkit and Supermini | ILI9341, XPT2046, SD over SPI with ESP-IDF's sdspi, MCP23017s (a chip reader, and buttons), ST7735, GPIO buttons, its encoder; the picture read back from the glass, touches, turns and button presses reaching the UI; on the devkit, Settings switching it from the encoder to its switches |
| [meshcore-bare-esp32s3](https://github.com/mytechnotalent/meshcore-bare-esp32s3) | ESP32-S3 + SX1262 | GP-SPI2, GPIO interrupts, the SX1262 and time on air, the air hub; NimBLE over virtual Bluetooth with a phone pairing. Two nodes advertise over LoRa and store each other as contacts |
| [LilyGO T-Deck](https://github.com/Xinyuan-LilyGO/T-Deck) (factory firmware) | T-Deck | The ST7789 under TFT_eSPI and LVGL, the GT911 and keyboard, RadioLib on the SX1262, PSRAM, USB Serial/JTAG, its peripherals' power switch, Wi-Fi start-up; its main loop: keys typed reach it, its radio sends, its screen answers |
| [LilyGO T-Display-S3](https://github.com/Xinyuan-LilyGO/T-Display-S3) (factory firmware) | T-Display-S3 | LCD_CAM and the GDMA under ESP-IDF's esp_lcd i80 driver, LVGL, octal PSRAM, LEDC, Wi-Fi start-up; its UI on the glass, its KEY button turning its pages |
| [wifi-start](../firmware/wifi-start) (a minimal ESP-IDF app) | DevKitC | Wi-Fi: start-up (the PHY's calibrations, the MAC's reset), then with an access point a scan, a connection, DHCP and a UDP round trip with the host |
| [SlimeVR tracker](https://github.com/HelaFaye/SlimeVR-Tracker-ESP) (BOARD_ESP32S3_SUPERMINI, Arduino-ESP32 3.3) | SuperMini | esp_image_verify() over the running app, the RMT behind its RGB LED, LittleFS, Wi-Fi by built-in credentials, DHCP, discovery by UDP broadcast and a session with a stand-in server |

What the test cases have found, so far (each fixed, with a qtest that reproduces it):
- QEMU's SD model in SPI mode answered CMD9/CMD10 only from the wrong state and CMD13
  with the SD bus's response (patch 0008), and carried an illegal command's error into
  the next command's R1 (0013): gbagate's card wouldn't mount.
- The GT911 dropped INT before the host had read the touch (0009): the T-Deck's touch.
- The S3's PHY calibration waited forever on a peak detector (0009), then on a TX
  DC-offset measurement, the temperature sensor, IQ estimation and the MAC's reset
  (0015): Wi-Fi start-up, and the T-Deck's whole main loop behind it; then on an ADC
  conversion (its battery reading, 0015).
- The T-Display-S3 panicked dividing by LEDC's clock divider, which read 0 (0018); then
  its screenshots came out sheared: QEMU's `screendump` padded each row of a 170-pixel
  screen (0016). The panel had it right all along.
- With frames moving, ESP-IDF 5.4's receive path followed descriptor registers the
  ported MAC left at 0, and panicked; the access point only reached one fixed station
  MAC, and the chip's was 00:00:00:00:00:00 (0019).
- The SlimeVR tracker found three gaps, each fixed here with a qtest where one can show it:
  - Every Arduino-ESP32 3.x sketch that sized or hashed itself (`ESP.getSketchSize()`,
    `getSketchMD5()`, OTA) jumped to 0xdeadbeef. Unmapping esp_image_verify()'s second
    view of the running app's flash cleared the page both views shared (0021).
  - Writing an RGB_BUILTIN LED hung `setup()`: the RMT was unimplemented, so its
    transmissions never ended (0022).
  - Discovery by broadcast learned 127.0.0.1 as the server: libslirp's reply to a
    broadcast carries the host's loopback address (0023).

  It also found three bugs in the tracker itself, fixed there: a boot loop from linking
  the legacy I2C driver beside Arduino 3.x's Wire, a background I2C scan that never
  worked, and built-in Wi-Fi credentials that were never tried.
- Encoder edges came faster than firmware polling each tick could follow (0014):
  gbagate's devkit encoder.
- In gbagate itself, its own QEMU checks found an ST7735 driver sending pixels low byte
  first, and no board mounting its SD card. On the devkit, the first change of Settings >
  input isn't applied (`pio/src/gate_app.c` fills `applied_input` in after the edit has
  stored the new mode): the UI asks for the five-way's keys while the hardware reads only
  the encoder, so the board can't leave Settings and the change is never saved. Reported
  to gbagate; `boards_check.py` notes it and goes on with a second change, which applies.

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
