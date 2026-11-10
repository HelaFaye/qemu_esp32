// The ESP-IDF Bluetooth host stack (Bluedroid, host-only: CONFIG_BT_CONTROLLER_DISABLED) over
// HCI H4 on a UART: in QEMU, a -serial chardev to a virtual controller (emulation/vbt).
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int uart_num;           // 2: the UART left free by the console (0) and an app link (1)
    int baud_rate;          // QEMU paces received bytes by it: use a high one (3000000)
} esp_qemu_hci_config_t;

#define ESP_QEMU_HCI_DEFAULT_CONFIG() { .uart_num = 2, .baud_rate = 3000000 }

// Installs the UART, starts the receiver and attaches the driver: call before
// esp_bluedroid_init(). The controller's address is the virtual controller's.
esp_err_t esp_qemu_hci_init(const esp_qemu_hci_config_t *config);

#ifdef __cplusplus
}
#endif
