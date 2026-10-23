# Phone → C3 command protocol (v1)

The phone's CoreBluetooth central and iOS system HID driver share a single
physical authenticated BLE connection. Firmware continues to expose the standard
HID keyboard/mouse service (0x1812), plus:

| Item | UUID | Direction |
| --- | --- | --- |
| Command service | `C3331AD1-A52D-4AA2-B342-D75D708EA001` | Primary service |
| Command | `C3331AD1-A52D-4AA2-B342-D75D708EA002` | Write with response, encrypted + authenticated |
| Result | `C3331AD1-A52D-4AA2-B342-D75D708EA003` | Read / notify |

The command is a UTF-8 JSON object shorter than 256 bytes, with a positive integer
`id` and an `op`. The App sends one command at a time and waits for the matching
execution notification. GATT write success alone is not an execution ACK.

```json
{"id":1,"op":"ping"}
{"id":2,"op":"move","x":15,"y":-8}
{"id":3,"op":"key","usage":44,"modifiers":8}
{"id":4,"op":"click"}
{"id":5,"op":"release"}
```

Example notification:

```json
{"id":2,"ok":true,"secure":true}
```

- `move`: relative `x`, `y`, integers in [-127,127]. These are mouse units, not pixels.
- `scroll`: integer `wheel` in [-127,127]; App restricts it further to [-10,10].
- `key`: USB HID usage in [0,101], modifier byte in [0,255]. `CMD=8`, `SHIFT=2`.
- `consumer`: only usage `545` (`0x0221`, AC Search) is allowed. Report 3 is a
  16-bit Consumer Control report, pressed for 80 ms then released. The Agent's
  `key SEARCH` action maps here and opens iPhone Spotlight from inside an app.
  This is separate from keyboard usage IDs and takes no modifiers.
- `click`: left button down, 30 ms, button up.
- `press` / `release`: controlled drag; release clears mouse and keyboard state.
  A held mouse button is automatically released after 1.5 seconds without input.
- `ping`: authenticated link readiness check without input.

Commands are copied into an eight-entry fixed-size queue. A worker performs HID
output outside the NimBLE host callback. Queue overflow is rejected. Disconnect
invalidates queued commands. The App does not automatically resend a command
when its four-second ACK deadline expires, because a successful tap/key could
have lost only its response.

`ok:true` confirms the C3 accepted the HID output call on a secure link. Only a
new screenshot showing the effect confirms that iOS/the target app acted on it.

The custom service is advertised in scan response data; standard HID remains in
the primary advertisement. On upgrade, Service Changed invalidates bonded iOS
GATT caches. The App can retrieve an already connected HID peripheral rather than
requiring another discovery advertisement. The board uses a fixed experimental
pairing PIN `123456`; do not leave it openly pairable in an untrusted setting.

Optional USB console diagnostics accept `status` and `hid {JSON}` (no id needed
for serial). These are setup/debug tools, not the runtime Agent transport. See
`scripts/serial_hid.py`. Never run serial diagnostics during an Agent acceptance
run; it would make input attribution ambiguous.

Development helpers verify the bound USB hardware serial before opening a port.
This matters when a C3 and S3 are connected concurrently: a reused port name must
fail identity validation before either board receives any command.
