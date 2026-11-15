/* Starts Wi-Fi as a station and scans: on the emulated S3 it reaches STA_START and
 * finds no networks (no frames move: there is no air for Wi-Fi). */
#include <stdio.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START)
        printf("wifi: station started\n");
    if (base == WIFI_EVENT && id == WIFI_EVENT_SCAN_DONE) {
        uint16_t n = 0;
        esp_wifi_scan_get_ap_num(&n);
        printf("wifi: scan done, %u networks\n", n);
    }
}

void app_main(void)
{
    nvs_flash_init();
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    printf("wifi: init %s\n", esp_err_to_name(esp_wifi_init(&cfg)));
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL);
    esp_wifi_set_mode(WIFI_MODE_STA);
    printf("wifi: start %s\n", esp_err_to_name(esp_wifi_start()));
    printf("wifi: scan %s\n", esp_err_to_name(esp_wifi_scan_start(NULL, false)));
}
