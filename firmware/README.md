# Real firmware on emulated boards

Each directory builds one project's firmware from its own repository at a pinned commit
(`COMMIT`), boots it in QEMU with the patches in `../patches/espressif-qemu` on the board it
was made for (`boards.py`), and checks what it prints. CI: `.github/workflows/firmware.yml`.

| Firmware | Board | Scenario |
|---|---|---|
| [meshcore-bare-esp32s3](https://github.com/mytechnotalent/meshcore-bare-esp32s3) | ESP32-S3 + SX1262 | `meshcore-bare/two_nodes.py`: two nodes on one air (`radio/airhub.py`) bring their radios up, advertise, hear each other and store each other as contacts |
| [gbagate](https://github.com/HelaFaye/gbagate) | its touch carrier (ESP32-S3 Supermini, ILI9341 + XPT2046, micro SD, MCP23017s) and its devkit (ST7735) | `gbagate/check.sh`, gbagate's own `qemu/accurate_boot.py`: the panel, touch, card and link PHYs come up; the picture is read back from the glass (the UI at 15/8, the touch keys, the theme's colours); a touch lights its key and moves the menu; touch alone reaches Send Program, which lists the program on the card |

## Emulator builds

Where a board has something QEMU can't be, an emulator build changes only that, in
the firmware's own configuration. The ESP32-S3's Bluetooth controller is radio hardware
inside the chip; its emulator build uses ESP-IDF's host-only Bluetooth (NimBLE or
Bluedroid over HCI on UART1), so the firmware's real Bluetooth host talks to a virtual
controller (`../bluetooth`) instead. The stock build is booted too, to show how far it
gets without one.

## Running locally

```
patches/espressif-qemu/build.sh ~/esp-qemu
export QEMU_XTENSA=~/esp-qemu/build/qemu-system-xtensa
firmware/meshcore-bare/build.sh /tmp/mcb                  # PlatformIO, esptool
firmware/meshcore-bare/two_nodes.py /tmp/mcb/emulator/flash.bin /tmp/mcb/run
```
