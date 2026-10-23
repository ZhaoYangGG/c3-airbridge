# Hardware isolation

This repository targets ESP32-C3. A different ESP32-S3 may be developed on the
same Mac at the same time. USB port names can change and be reused by another
board. Never select a device by a remembered port name or automatic esptool scan.

Before opening any serial port, resolve the private `.local/c3-device.json`
binding using `scripts/device_guard.py`. Its USB serial must match the live
IOKit parent of that port. Do not commit hardware identities. Use the guarded
`scripts/flash_c3.py` and `scripts/serial_hid.py` helpers. If the binding is absent
or mismatches, stop hardware actions and inspect USB metadata without resetting
other boards. Keep the ESP-IDF target `esp32c3`; preserve NVS and bonds.

Do not use serial HID commands during an autonomous Agent acceptance run. The
phone App must be the sole scheduler/input sender; Mac tools may read its private
diagnostics. Record failures honestly and never count legacy Mac/AirPlay tasks
as successful tests of the native phone architecture.
