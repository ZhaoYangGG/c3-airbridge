# Third-party notices

## ESP-IDF HID GAP example

`firmware/main/esp_hid_gap.c` and `esp_hid_gap.h` originate from Espressif
ESP-IDF v6.0.1, commit `8c19b156084a0753687347cca1f5355782893533`, under
`examples/bluetooth/esp_hid_device/main/`.

Copyright 2021–2025 Espressif Systems (Shanghai) CO LTD. License:
**Unlicense OR CC0-1.0**; this project selects CC0-1.0. Original headers remain.
See <https://creativecommons.org/publicdomain/zero/1.0/legalcode>.

Local changes keep advertising enabled, initiate authenticated pairing, expose
live link security state, advertise the App command service in the scan response,
and refresh bonded clients after GATT database changes. The automatic HID demo
is disabled; input requires explicit commands.

ESP-IDF, Apache NimBLE and cJSON retain their upstream licenses. The iOS app uses
Apple system frameworks; it has no third-party Swift package dependencies.

[ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) inspired
the hardware automation exploration. No code from that project is copied here.

UxPlay and its derivative patches are no longer part of main. Their notices and
licenses remain with the files in the `legacy-airplay-v0.2` Git tag.
