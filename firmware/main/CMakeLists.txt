idf_component_register(SRCS "main.c" "relay.c" "hid.c" "esp_hid_gap.c"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_netif nvs_flash esp_hid bt esp_timer)
target_link_options(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=ble_svc_hid_add")
