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

## Open-AutoGLM Swift adaptation

`ios/C3Agent/AutoGLM.swift`, `ios/C3Agent/Agent.swift` and the adapted
`ios/C3Agent/AgentPrompt.txt` implement a Swift/C3 adaptation of the phone-agent
protocol, context lifecycle and decision loop from
[zai-org/Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM), pinned at
`86f55382982fb054e8fc98ca80609dff8a2cdc3c` (Apache-2.0).
These adapted components are distributed under Apache-2.0; the project's MIT
license continues to apply to other original project code. See
`licenses/Open-AutoGLM-APACHE-2.0.txt` for the complete license.

Local changes replace Python/WDA with native Swift scheduling, the existing
ScreenCaptureKit stream and C3 BLE HID; split decision and auxiliary model
profiles; parse action literals without evaluating code; retain execution
feedback and current-screen verification; and expose C3-specific action limits
and timing diagnostics. This is not an unmodified upstream runtime or a claim of
upstream model/device acceptance. No upstream model weights are bundled.

[ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) inspired
the hardware automation exploration. No code from that project is copied here.

UxPlay and its derivative patches are no longer part of main. Their notices and
licenses remain with the files in the `legacy-airplay-v0.2` Git tag.
