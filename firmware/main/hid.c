// SPDX-License-Identifier: MIT
#include <string.h>
#include "airbridge.h"
#include "esp_hidd.h"
#include "esp_hid_gap.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "services/gap/ble_svc_gap.h"
#include "services/hid/ble_svc_hid.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static esp_hidd_dev_t *device;
static volatile uint16_t connection_handle=BLE_HS_CONN_HANDLE_NONE;
static uint8_t buttons;
// ESP-IDF 6.0.1 supplies the Battery *service* UUID (0x180f) here.
// HOGP's External Report Reference must point to a characteristic UUID.
int __real_ble_svc_hid_add(struct ble_svc_hid_params params);
int __wrap_ble_svc_hid_add(struct ble_svc_hid_params params) {
    params.external_rpt_ref=0x2a19; // Battery Level characteristic in SDK BAS.
    return __real_ble_svc_hid_add(params);
}
// The CC0 ESP-IDF GAP helper calls this after encryption. This project only
// sends input on explicit authenticated commands, never a demo on pairing.
void ble_hid_task_start_up(void) { /* Read the live security descriptor below. */ }
// GAP callbacks run synchronously; ESP_HIDD_* events are queued. An old queued
// CONNECT event must not clear security after encryption has already completed.
void bridge_hid_gap_connected(uint16_t handle) {connection_handle=handle;}
void bridge_hid_gap_disconnected(void) {connection_handle=BLE_HS_CONN_HANDLE_NONE;buttons=0;}
void hid_link_state(bool *link,bool *encrypted,bool *authenticated) {
    struct ble_gap_conn_desc desc;
    *link=connection_handle!=BLE_HS_CONN_HANDLE_NONE && ble_gap_conn_find(connection_handle,&desc)==0;
    *encrypted=*link && desc.sec_state.encrypted;
    *authenticated=*link && desc.sec_state.authenticated;
}
bool hid_connected(void) {
    bool link,encrypted,authenticated;hid_link_state(&link,&encrypted,&authenticated);
    return link && encrypted && authenticated;
}
static const uint8_t reports[]={
    // Mouse, report 1: buttons, relative X/Y, wheel.
    0x05,0x01,0x09,0x02,0xa1,0x01,0x85,0x01,0x09,0x01,0xa1,0x00,
    0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,0x95,0x03,0x75,0x01,0x81,0x02,
    0x95,0x01,0x75,0x05,0x81,0x03,0x05,0x01,0x09,0x30,0x09,0x31,0x09,0x38,
    0x15,0x81,0x25,0x7f,0x75,0x08,0x95,0x03,0x81,0x06,0xc0,0xc0,
    // Keyboard, report 2: modifiers, reserved, six key usages.
    0x05,0x01,0x09,0x06,0xa1,0x01,0x85,0x02,0x05,0x07,0x19,0xe0,0x29,0xe7,
    0x15,0x00,0x25,0x01,0x75,0x01,0x95,0x08,0x81,0x02,
    0x95,0x01,0x75,0x08,0x81,0x03,0x95,0x06,0x75,0x08,
    0x15,0x00,0x25,0x65,0x05,0x07,0x19,0x00,0x29,0x65,0x81,0x00,0xc0
};
static esp_hid_raw_report_map_t maps[]={{.data=reports,.len=sizeof(reports)}};
static esp_hid_device_config_t config={
    .vendor_id=0x303a,.product_id=0x4001,.version=0x0100,
    .device_name="C3 AirBridge",.manufacturer_name="c3-airbridge",
    .serial_number="experimental",.report_maps=maps,.report_maps_len=1
};

static bool mouse(int x,int y,int wheel) {
    uint8_t data[]={buttons,(uint8_t)x,(uint8_t)y,(uint8_t)wheel};
    return hid_connected() && esp_hidd_dev_input_set(device,0,1,data,sizeof(data))==ESP_OK;
}
void hid_release(void) {
    buttons=0;
    if(hid_connected()) {
        mouse(0,0,0);
        uint8_t keys[8]={0}; esp_hidd_dev_input_set(device,0,2,keys,sizeof(keys));
    }
}
static void callback(void *arg,esp_event_base_t base,int32_t id,void *data) {
    if(id==ESP_HIDD_START_EVENT) {
        ESP_ERROR_CHECK(ble_svc_gap_device_name_set(config.device_name));
        ESP_ERROR_CHECK(ble_svc_gap_device_appearance_set(ESP_HID_APPEARANCE_KEYBOARD));
        esp_hid_ble_gap_adv_start();
    }
    else if(id==ESP_HIDD_DISCONNECT_EVENT) {
        if(connection_handle==BLE_HS_CONN_HANDLE_NONE)esp_hid_ble_gap_adv_start();
    }
}
static void host(void *arg) {nimble_port_run(); nimble_port_freertos_deinit();}
void ble_store_config_init(void);
void hid_init(void) {
    ESP_ERROR_CHECK(esp_hid_gap_init(HIDD_BLE_MODE));
    ESP_ERROR_CHECK(esp_hid_ble_gap_adv_init(ESP_HID_APPEARANCE_KEYBOARD,config.device_name));
    ESP_ERROR_CHECK(esp_hidd_dev_init(&config,ESP_HID_TRANSPORT_BLE,callback,&device));
    ble_store_config_init(); ble_hs_cfg.store_status_cb=ble_store_util_status_rr;
    ESP_ERROR_CHECK(esp_nimble_enable(host));
}
static bool number(cJSON *o,const char *key,int *out,int lo,int hi) {
    cJSON *v=cJSON_GetObjectItemCaseSensitive(o,key);
    if(!cJSON_IsNumber(v)||v->valuedouble!=v->valueint||v->valueint<lo||v->valueint>hi) return false;
    *out=v->valueint; return true;
}
bool hid_command(cJSON *o) {
    if(!hid_connected()) return false;
    cJSON *op=cJSON_GetObjectItemCaseSensitive(o,"op");
    if(!cJSON_IsString(op)) return false;
    if(!strcmp(op->valuestring,"move")) {
        int x,y; if(!number(o,"x",&x,-127,127)||!number(o,"y",&y,-127,127))return false;
        return mouse(x,y,0);
    }
    if(!strcmp(op->valuestring,"scroll")) {
        int n; return number(o,"wheel",&n,-127,127)&&mouse(0,0,n);
    }
    if(!strcmp(op->valuestring,"release")) {hid_release(); return true;}
    if(!strcmp(op->valuestring,"press")||!strcmp(op->valuestring,"click")) {
        buttons=1; bool ok=mouse(0,0,0);
        if(!strcmp(op->valuestring,"click")) {
            vTaskDelay(pdMS_TO_TICKS(30)); buttons=0; ok=mouse(0,0,0)&&ok;
        }
        return ok;
    }
    if(!strcmp(op->valuestring,"key")) {
        int usage,mod=0;
        if(!number(o,"usage",&usage,0,101))return false;
        if(cJSON_HasObjectItem(o,"modifiers")&&!number(o,"modifiers",&mod,0,255))return false;
        uint8_t data[8]={(uint8_t)mod,0,(uint8_t)usage};
        bool ok=esp_hidd_dev_input_set(device,0,2,data,sizeof(data))==ESP_OK;
        vTaskDelay(pdMS_TO_TICKS(50)); memset(data,0,sizeof(data));
        return esp_hidd_dev_input_set(device,0,2,data,sizeof(data))==ESP_OK&&ok;
    }
    return false;
}
