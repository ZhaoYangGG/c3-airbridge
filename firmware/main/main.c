#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "airbridge.h"

bridge_config_t bridge_config;
volatile bool bridge_wifi_ready;
static const char *TAG = "airbridge";

void print_status(void) {
    printf("AIRBRIDGE_STATUS {\"firmware\":\"0.2.0\",\"target\":\"esp32c3\","
           "\"wifi\":%s,\"ble\":%s,\"heap\":%lu,\"min_heap\":%lu,\"uptime_ms\":%lld}\n",
           bridge_wifi_ready ? "true" : "false", hid_connected() ? "true" : "false",
           (unsigned long)esp_get_free_heap_size(), (unsigned long)esp_get_minimum_free_heap_size(),
           esp_timer_get_time()/1000);
    fflush(stdout);
}

static bool field(cJSON *j, const char *key, char *dst, size_t size, bool empty) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(j, key);
    if (!cJSON_IsString(v) || strlen(v->valuestring) >= size || (!empty && !v->valuestring[0])) return false;
    strcpy(dst, v->valuestring);
    return true;
}

static bool configure(const char *line) {
    cJSON *j = cJSON_Parse(line);
    if (!j) return false;
    bridge_config_t cfg = {0};
    cJSON *port = cJSON_GetObjectItemCaseSensitive(j, "port");
    bool ok = field(j,"ssid",cfg.ssid,sizeof(cfg.ssid),false) &&
        field(j,"password",cfg.password,sizeof(cfg.password),true) &&
        field(j,"host",cfg.host,sizeof(cfg.host),false) &&
        field(j,"token",cfg.token,sizeof(cfg.token),false) && strlen(cfg.token)>=24 &&
        cJSON_IsNumber(port) && port->valuedouble == port->valueint && port->valueint>0 && port->valueint<=65535;
    if (ok) {
        cfg.port = port->valueint;
        nvs_handle_t h;
        ok = nvs_open("airbridge",NVS_READWRITE,&h)==ESP_OK;
        if(ok) {
            ok = nvs_set_blob(h,"config",&cfg,sizeof(cfg))==ESP_OK && nvs_commit(h)==ESP_OK;
            nvs_close(h);
        }
    }
    cJSON_Delete(j);
    memset(&cfg,0,sizeof(cfg));
    return ok;
}

static void console_task(void *arg) {
    char line[1024]; size_t used=0; bool overflow=false;
    for (;;) {
        int ch = getchar();
        if(ch==EOF) { clearerr(stdin); vTaskDelay(pdMS_TO_TICKS(20)); continue; }
        if(ch=='\r' || ch=='\n') {
            line[used]=0;
            if(!overflow && used) {
                if(!strcmp(line,"status")) print_status();
                else if(!strncmp(line,"configure ",10)) {
                    if(configure(line+10)) {
                        printf("AIRBRIDGE_CONFIG_OK\n"); fflush(stdout);
                        memset(line,0,sizeof(line));
                        vTaskDelay(pdMS_TO_TICKS(300)); esp_restart();
                    } else printf("AIRBRIDGE_CONFIG_ERROR\n");
                } else printf("AIRBRIDGE_COMMAND_ERROR\n");
            }
            memset(line,0,sizeof(line)); used=0; overflow=false;
        } else if(used<sizeof(line)-1) line[used++]=(char)ch;
        else overflow=true;
    }
}

static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data) {
    if(base==WIFI_EVENT && id==WIFI_EVENT_STA_START) esp_wifi_connect();
    else if(base==WIFI_EVENT && id==WIFI_EVENT_STA_DISCONNECTED) {
        bridge_wifi_ready=false; esp_wifi_connect();
    } else if(base==IP_EVENT && id==IP_EVENT_STA_GOT_IP) {
        bridge_wifi_ready=true;
        ESP_LOGI(TAG,"Wi-Fi connected; relay can start");
        print_status();
    }
}

void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    nvs_handle_t h; size_t size=sizeof(bridge_config);
    bool loaded=false;
    if(nvs_open("airbridge",NVS_READONLY,&h)==ESP_OK) {
        loaded=nvs_get_blob(h,"config",&bridge_config,&size)==ESP_OK && size==sizeof(bridge_config);
        nvs_close(h);
    }
    setvbuf(stdin,NULL,_IONBF,0);
    esp_log_level_set("NimBLE",ESP_LOG_WARN);
    hid_init();
    xTaskCreate(console_task,"provision",4096,NULL,3,NULL);
    if(!loaded) {
        ESP_LOGI(TAG,"Waiting for local USB provisioning; no Wi-Fi credentials configured");
        print_status(); return;
    }
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL));
    wifi_config_t wifi={0};
    memcpy(wifi.sta.ssid,bridge_config.ssid,strlen(bridge_config.ssid));
    memcpy(wifi.sta.password,bridge_config.password,strlen(bridge_config.password));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&wifi));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    xTaskCreate(relay_task,"relay",10240,NULL,4,NULL);
}
