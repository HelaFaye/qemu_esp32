# esp_qemu_hci: the Bluetooth host stack over HCI on a UART, for running Bluetooth firmware
# in QEMU (which has no radio) against a virtual controller (emulation/vbt). Bluedroid needs
# this driver; NimBLE has its own UART transport (see README).
idf_component_register(SRCS "esp_qemu_hci.c"
                       INCLUDE_DIRS "include"
                       REQUIRES bt esp_driver_uart)
