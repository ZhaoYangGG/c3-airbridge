# Migration from AirPlay relay 0.2 to phone Agent 1.0

The previous architecture is retained in Git tag `legacy-airplay-v0.2`, based on
commit `cdd1ebf`. A local Git bundle backup was made before cleanup. Check out that
tag to inspect/build the old implementation; do not mix its services with 1.0.

The new main branch removes the Python backend/vision scheduler, UxPlay processes
and patches, RTSP/TCP video relay, Wi-Fi provisioning UI, cloud backend unit,
LaunchAgent installer/supervisor, and tests for those removed components. Video
now originates on the phone using ScreenCaptureKit; the phone calls the model
API and sends direct BLE commands to C3.

Old Wi-Fi configuration may remain in board NVS after a normal flash. New firmware
never reads or uses it. Avoid erasing all flash just for this cleanup, because NVS
also stores Bluetooth bonds. A normal app flash preserves pairing.

If you installed the old macOS stack, stop/unload its LaunchAgent and any known
children before testing. Its runtime directory can contain credentials and phone
screenshots: keep it private and do not commit it. Repository cleanup does not
require deleting those private records or credentials. Retire the old Desktop
launcher so it cannot accidentally start an obsolete backend.

For an iPhone that already paired with the old board, first keep the existing
bond. The new firmware sends Service Changed and the new app can retrieve the
connected HID peripheral. Only if discovery remains stuck should you forget the
old C3 Bluetooth entry and pair again as C3 Agent. First-time iOS Bluetooth,
network access and screen-sharing prompts must be completed normally.

The App retains the prototype Bundle ID by default so the installed probe can be
upgraded in place. Its display name is now C3 Agent. It uses Keychain for the model
key, and no longer needs AirPlay screen mirroring. At runtime, USB supplies power;
C3 input uses Bluetooth. Phone Internet access is needed only for the configured
model service.
