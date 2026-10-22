# Third-party notices

## ESP-IDF HID GAP example

Files `firmware/main/esp_hid_gap.c` and `esp_hid_gap.h` originate from Espressif
ESP-IDF v6.0.1, commit `8c19b156084a0753687347cca1f5355782893533`, under
`examples/bluetooth/esp_hid_device/main/`.

Copyright 2021–2025 Espressif Systems (Shanghai) CO LTD (source-specific dates
are retained in file headers). License: **Unlicense OR CC0-1.0**. We select CC0-1.0.
See <https://creativecommons.org/publicdomain/zero/1.0/legalcode>.

These files retain their original license headers. The surrounding application
does not run the example's automatic input demo.

## Separately installed software

- [UxPlay](https://github.com/FDH2/UxPlay), GPL-3.0: separately built/run process;
  no UxPlay source is copied or linked into this firmware/backend.
- ESP-IDF, NimBLE, cJSON and mDNS retain their own upstream licenses.
- [ESP32 iPhone Agent by Henry Yuan](https://github.com/onehumanbeing/esp32-iphone-agent)
  inspired the architecture and is the comparison baseline. No code from that
  repository is copied into this prototype.
