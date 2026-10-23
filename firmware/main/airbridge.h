#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"

typedef struct {
    char ssid[33], password[65], host[128], token[129];
    uint16_t port;
} bridge_config_t;
extern bridge_config_t bridge_config;
extern volatile bool bridge_wifi_ready;
void relay_task(void *arg);
void hid_init(void);
void hid_release(void);
bool hid_connected(void);
void hid_link_state(bool *link,bool *encrypted,bool *authenticated);
bool hid_command(cJSON *obj);
void print_status(void);
