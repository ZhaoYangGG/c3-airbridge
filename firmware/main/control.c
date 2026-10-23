// SPDX-License-Identifier: MIT
#include <string.h>
#include <stdio.h>
#include "airbridge.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/ble_gatt.h"
#include "os/os_mbuf.h"

// C3 Agent service ...A001, command ...A002, result ...A003 (little endian).
#define UUID_BYTES(last) last,0xa0,0x8e,0x70,0x5d,0xd7,0x42,0xb3,0xa2,0x4a,0x2d,0xa5,0xd1,0x1a,0x33,0xc3
static const ble_uuid128_t service_uuid=BLE_UUID128_INIT(UUID_BYTES(0x01));
static const ble_uuid128_t command_uuid=BLE_UUID128_INIT(UUID_BYTES(0x02));
static const ble_uuid128_t result_uuid=BLE_UUID128_INIT(UUID_BYTES(0x03));
static uint16_t result_handle;
typedef struct {uint16_t connection;uint32_t generation;char json[256];} command_t;
static QueueHandle_t commands;
static volatile uint32_t generation;

void control_disconnected(void){generation++;if(commands)xQueueReset(commands);}

static int access_control(uint16_t conn,uint16_t handle,struct ble_gatt_access_ctxt *ctxt,void *arg) {
    if(ctxt->op==BLE_GATT_ACCESS_OP_READ_CHR) {
        char data[96];int n=snprintf(data,sizeof(data),"{\"version\":1,\"firmware\":\"1.0.0\",\"secure\":%s}",hid_connected()?"true":"false");
        return os_mbuf_append(ctxt->om,data,n)==0?0:BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    if(ctxt->op!=BLE_GATT_ACCESS_OP_WRITE_CHR||!hid_connected())return BLE_ATT_ERR_INSUFFICIENT_AUTHEN;
    command_t item={.connection=conn,.generation=generation};uint16_t length=OS_MBUF_PKTLEN(ctxt->om);
    if(!length||length>=sizeof(item.json))return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    if(ble_hs_mbuf_to_flat(ctxt->om,item.json,sizeof(item.json)-1,NULL))return BLE_ATT_ERR_UNLIKELY;
    cJSON *j=cJSON_Parse(item.json),*id=j?cJSON_GetObjectItemCaseSensitive(j,"id"):NULL;
    bool valid=cJSON_IsObject(j)&&cJSON_IsNumber(id)&&id->valuedouble==id->valueint&&id->valueint>0;
    cJSON_Delete(j);
    if(!valid)return BLE_ATT_ERR_UNLIKELY;
    return xQueueSend(commands,&item,0)==pdTRUE?0:BLE_ATT_ERR_INSUFFICIENT_RES;
}

static const struct ble_gatt_svc_def services[]={
    {.type=BLE_GATT_SVC_TYPE_PRIMARY,.uuid=&service_uuid.u,.characteristics=(struct ble_gatt_chr_def[]){
        {.uuid=&command_uuid.u,.access_cb=access_control,.flags=BLE_GATT_CHR_F_WRITE|BLE_GATT_CHR_F_WRITE_ENC|BLE_GATT_CHR_F_WRITE_AUTHEN},
        {.uuid=&result_uuid.u,.access_cb=access_control,.val_handle=&result_handle,.flags=BLE_GATT_CHR_F_READ|BLE_GATT_CHR_F_READ_ENC|BLE_GATT_CHR_F_NOTIFY},
        {0}}}, {0}};

static void worker(void *arg) {
    command_t item;
    for(;;) {
        if(xQueueReceive(commands,&item,pdMS_TO_TICKS(250))!=pdTRUE){hid_housekeeping();continue;}
        uint32_t before=item.generation;
        if(before!=generation)continue;
        cJSON *j=cJSON_Parse(item.json),*id=j?cJSON_GetObjectItemCaseSensitive(j,"id"):NULL;
        cJSON *op=j?cJSON_GetObjectItemCaseSensitive(j,"op"):NULL;
        bool ping=cJSON_IsString(op)&&!strcmp(op->valuestring,"ping");
        bool ok=hid_connected()&&(ping||hid_command(j));
        char result[96];int n=snprintf(result,sizeof(result),"{\"id\":%d,\"ok\":%s,\"secure\":%s}",id?id->valueint:0,ok?"true":"false",hid_connected()?"true":"false");
        cJSON_Delete(j);
        if(before==generation) {
            struct os_mbuf *m=ble_hs_mbuf_from_flat(result,n);
            if(m)ble_gatts_notify_custom(item.connection,result_handle,m);
        }
    }
}

void control_init(void) {
    commands=xQueueCreate(8,sizeof(command_t));assert(commands);
    assert(ble_gatts_count_cfg(services)==0);
    assert(ble_gatts_add_svcs(services)==0);
    assert(xTaskCreate(worker,"phone_commands",4096,NULL,3,NULL)==pdPASS);
}
