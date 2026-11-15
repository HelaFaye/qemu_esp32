/* Wi-Fi on the emulated S3, as a station: it starts and scans. With no network
 * attached (boot()'s default) the scan finds nothing: no frames move. With an access
 * point (boot(..., wifi=True): -nic user,model=esp32_wifi) it lists the networks,
 * connects to "Open Wifi", gets an address by DHCP, and does a UDP round trip with the
 * host (QEMU's user-mode network: the host is 10.0.2.2), to port 5555. */
#include <stdio.h>
#include <string.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"

#define SSID "Open Wifi"

static void udp_round_trip(void *arg)
{
    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in host = { .sin_family = AF_INET, .sin_port = htons(5555) };
    inet_aton("10.0.2.2", &host.sin_addr);
    struct timeval tv = { .tv_sec = 2 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    for (int i = 0; i < 10; i++) {
        char msg[32], reply[64];
        int n = snprintf(msg, sizeof msg, "hello %d", i);
        sendto(fd, msg, n, 0, (struct sockaddr *)&host, sizeof host);
        int r = recv(fd, reply, sizeof reply - 1, 0);
        if (r > 0) {
            reply[r] = 0;
            printf("udp: sent \"%s\", got \"%s\"\n", msg, reply);
            break;
        }
        printf("udp: no reply to \"%s\"\n", msg);
    }
    close(fd);
    vTaskDelete(NULL);
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START)
        printf("wifi: station started\n");
    if (base == WIFI_EVENT && id == WIFI_EVENT_SCAN_DONE) {
        uint16_t n = 0;
        esp_wifi_scan_get_ap_num(&n);
        printf("wifi: scan done, %u networks\n", n);
        static wifi_ap_record_t aps[16];      /* Not on the event task's small stack */
        uint16_t got = 16;
        bool open = false;
        esp_wifi_scan_get_ap_records(&got, aps);
        for (int i = 0; i < got; i++) {
            printf("wifi: network \"%s\" channel %d rssi %d\n", (char *)aps[i].ssid,
                   aps[i].primary, aps[i].rssi);
            open |= !strcmp((char *)aps[i].ssid, SSID);
        }
        if (open) {
            wifi_config_t sta = { .sta = { .ssid = SSID } };
            esp_wifi_set_config(WIFI_IF_STA, &sta);
            printf("wifi: connect %s\n", esp_err_to_name(esp_wifi_connect()));
        }
    }
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED)
        printf("wifi: connected to " SSID "\n");
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED)
        printf("wifi: disconnected, reason %d\n",
               ((wifi_event_sta_disconnected_t *)data)->reason);
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        printf("wifi: got ip " IPSTR " gateway " IPSTR "\n", IP2STR(&e->ip_info.ip),
               IP2STR(&e->ip_info.gw));
        xTaskCreate(udp_round_trip, "udp", 4096, NULL, 5, NULL);
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
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL);
    esp_wifi_set_mode(WIFI_MODE_STA);
    printf("wifi: start %s\n", esp_err_to_name(esp_wifi_start()));
    printf("wifi: scan %s\n", esp_err_to_name(esp_wifi_scan_start(NULL, false)));
}
