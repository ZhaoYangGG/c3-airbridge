#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"

void hid_init(void);
void hid_release(void);
void hid_housekeeping(void);
bool hid_connected(void);
void hid_link_state(bool *link,bool *encrypted,bool *authenticated);
bool hid_command(cJSON *obj);
void print_status(void);
void control_init(void);
void control_disconnected(void);
