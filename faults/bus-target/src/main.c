/* An MCP23017 at 0x20 on I2C0 (SDA 1, SCL 2), written and read back over and over with
 * ESP-IDF's I2C master driver: OLATA <- n, then read OLATA. Every 200 operations:
 *     i2c ops=200 ok=<n> nack=<n> timeout=<n> other=<n> mismatch=<n>
 * (nack: a transaction that didn't finish, which is how ESP-IDF 5's i2c_master reports a
 * missing ACK (ESP_ERR_INVALID_STATE); mismatch: read back wrong). */
#include <stdio.h>
#include "driver/i2c_master.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

void app_main(void)
{
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = 0, .sda_io_num = 1, .scl_io_num = 2,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_cfg, &bus));
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = 0x20, .scl_speed_hz = 400000,
    };
    i2c_master_dev_handle_t dev;
    ESP_ERROR_CHECK(i2c_master_bus_add_device(bus, &dev_cfg, &dev));

    /* GPA as outputs */
    uint8_t iodir[2] = { 0x00, 0x00 };
    i2c_master_transmit(dev, iodir, 2, 100);
    printf("bus target ready\n");

    int ok = 0, nack = 0, timeout = 0, other = 0, mismatch = 0;
    for (uint32_t n = 0;; n++) {
        uint8_t v = (uint8_t)(n * 37 + 11);
        uint8_t w[2] = { 0x14, v }, reg = 0x14, r = 0;      /* OLATA */
        esp_err_t err = i2c_master_transmit(dev, w, 2, 100);
        if (err == ESP_OK) {
            err = i2c_master_transmit_receive(dev, &reg, 1, &r, 1, 100);
        }
        if (err == ESP_OK) {
            if (r == v) {
                ok++;
            } else {
                mismatch++;
            }
        } else if (err == ESP_ERR_TIMEOUT) {
            timeout++;
        } else if (err == ESP_ERR_INVALID_STATE || err == ESP_ERR_NOT_FOUND || err == ESP_FAIL) {
            nack++;
        } else {
            other++;
            printf("error %s\n", esp_err_to_name(err));
        }
        if ((n + 1) % 200 == 0) {
            printf("i2c ops=200 ok=%d nack=%d timeout=%d other=%d mismatch=%d\n",
                   ok, nack, timeout, other, mismatch);
            ok = nack = timeout = other = mismatch = 0;
            vTaskDelay(1);
        }
    }
}
