/* A counter in NVS, incremented and committed every 10 ms. At boot it prints
 *     boot reason=<esp_reset_reason()> counter=<n>
 * and after each commit
 *     committed <n>
 * so a test can cut the power anywhere and check the counter it reads back is never
 * below the last value reported committed, and NVS always opens. */
#include <stdio.h>
#include <inttypes.h>
#include "esp_system.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *reason(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SW: return "sw";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: case ESP_RST_WDT: return "wdt";
    default: return "other";
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        printf("nvs needs erase: %s\n", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        printf("nvs init failed: %s\n", esp_err_to_name(err));
        return;
    }
    nvs_handle_t h;
    ESP_ERROR_CHECK(nvs_open("power", NVS_READWRITE, &h));
    uint32_t n = 0;
    err = nvs_get_u32(h, "counter", &n);
    if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
        printf("nvs read failed: %s\n", esp_err_to_name(err));
    }
    printf("boot reason=%s counter=%" PRIu32 "\n", reason(esp_reset_reason()), n);
    for (;;) {
        n++;
        ESP_ERROR_CHECK(nvs_set_u32(h, "counter", n));
        ESP_ERROR_CHECK(nvs_commit(h));
        printf("committed %" PRIu32 "\n", n);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
