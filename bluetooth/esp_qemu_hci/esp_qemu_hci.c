#include "esp_qemu_hci.h"

#include <string.h>

#include "driver/uart.h"
#include "esp_bluedroid_hci.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "qemu_hci";

enum { H4_CMD = 1, H4_ACL = 2, H4_SCO = 3, H4_EVT = 4 };

static int s_uart = -1;
static SemaphoreHandle_t s_tx_lock;
static esp_bluedroid_hci_driver_callbacks_t s_cb;

static bool read_exact(uint8_t *buf, size_t len)
{
    size_t got = 0;
    while (got < len) {
        int n = uart_read_bytes(s_uart, buf + got, len - got, portMAX_DELAY);
        if (n < 0)
            return false;
        got += (size_t)n;
    }
    return true;
}

// H4 packets from the controller: type, header, payload. An unknown type byte is skipped
// (a resync), instead of asserting as Espressif's example does.
static void rx_task(void *arg)
{
    static uint8_t buf[1 + 4 + 1024 + 8];
    for (;;) {
        if (!read_exact(buf, 1))
            continue;
        size_t hdr, len;
        switch (buf[0]) {
        case H4_EVT: hdr = 2; break;
        case H4_ACL: hdr = 4; break;
        case H4_SCO: hdr = 3; break;
        default:
            ESP_LOGW(TAG, "skipping 0x%02x (not an H4 packet type)", buf[0]);
            continue;
        }
        if (!read_exact(buf + 1, hdr))
            continue;
        len = buf[0] == H4_EVT ? buf[2] : buf[0] == H4_SCO ? buf[3] : (size_t)(buf[3] | (buf[4] << 8));
        if (1 + hdr + len > sizeof(buf)) {
            ESP_LOGE(TAG, "packet too long (%u): dropped", (unsigned)len);
            uint8_t sink[64];
            while (len) {
                size_t n = len < sizeof(sink) ? len : sizeof(sink);
                read_exact(sink, n);
                len -= n;
            }
            continue;
        }
        if (len && !read_exact(buf + 1 + hdr, len))
            continue;
        if (s_cb.notify_host_recv)
            s_cb.notify_host_recv(buf, (uint16_t)(1 + hdr + len));
    }
}

static void hci_send(uint8_t *data, uint16_t len)
{
    xSemaphoreTake(s_tx_lock, portMAX_DELAY);
    while (len) {
        int n = uart_write_bytes(s_uart, data, len);
        if (n <= 0)
            break;
        data += n;
        len -= (uint16_t)n;
    }
    xSemaphoreGive(s_tx_lock);
}

static bool hci_send_available(void)
{
    return true;                // The UART driver's buffer queues; the host's ACL credits pace
}

static esp_err_t hci_register(const esp_bluedroid_hci_driver_callbacks_t *cb)
{
    s_cb = *cb;
    return ESP_OK;
}

static const esp_bluedroid_hci_driver_operations_t s_ops = {
    .send = hci_send,
    .check_send_available = hci_send_available,
    .register_host_callback = hci_register,
};

esp_err_t esp_qemu_hci_init(const esp_qemu_hci_config_t *config)
{
    uart_config_t uc = {
        .baud_rate = config->baud_rate,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(config->uart_num, 4096, 4096, 0, NULL, 0);
    if (err == ESP_OK)
        err = uart_param_config(config->uart_num, &uc);
    if (err != ESP_OK)
        return err;
    s_uart = config->uart_num;
    s_tx_lock = xSemaphoreCreateMutex();
    if (xTaskCreatePinnedToCore(rx_task, "qemu_hci_rx", 4096, NULL, 12, NULL, 0) != pdPASS)
        return ESP_ERR_NO_MEM;
    return esp_bluedroid_attach_hci_driver(&s_ops);
}
