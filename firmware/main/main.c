// SPDX-License-Identifier: MIT
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "airbridge.h"
void print_status(void) {
    printf("C3_AGENT_STATUS {\"firmware\":\"1.0.0\",\"target\":\"esp32c3\",\"ble\":%s,\"heap\":%lu,\"uptime_ms\":%lld}\n",hid_connected()?"true":"false",(unsigned long)esp_get_free_heap_size(),esp_timer_get_time()/1000);fflush(stdout);
}
static void console(void *arg) {
    char line[320];size_t n=0;bool overflow=false;
    for(;;) {
        int ch=getchar();
        if(ch==EOF){clearerr(stdin);vTaskDelay(pdMS_TO_TICKS(20));continue;}
        if(ch=='\n'||ch=='\r') {
            line[n]=0;
            if(!overflow&&n) {
                if(!strcmp(line,"status")) print_status();
                else if(!strncmp(line,"hid ",4)) {
                    cJSON *j=cJSON_Parse(line+4);bool ok=j&&hid_command(j);cJSON_Delete(j);
                    printf("C3_AGENT_HID {\"ok\":%s}\n",ok?"true":"false");fflush(stdout);
                }
            }
            memset(line,0,sizeof(line));n=0;overflow=false;
        }else if(n<sizeof(line)-1)line[n++]=(char)ch;else overflow=true;
    }
}
void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());setvbuf(stdin,NULL,_IONBF,0);esp_log_level_set("NimBLE",ESP_LOG_WARN);
    hid_init();xTaskCreate(console,"diagnostics",4096,NULL,2,NULL);print_status();
}
