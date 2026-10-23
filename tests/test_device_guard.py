"""USB port reuse must never select the concurrently developed S3."""
import importlib.util
import json
import plistlib
import tempfile
from unittest.mock import patch
from pathlib import Path

spec=importlib.util.spec_from_file_location("guard",Path(__file__).parents[1]/"scripts/device_guard.py")
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
tree={"IORegistryEntryChildren":[
    {"USB Serial Number":"C3-IDENTITY","IORegistryEntryChildren":[{"IOCalloutDevice":"/dev/cu.usbmodem11401"}]},
    {"USB Serial Number":"S3-IDENTITY","IORegistryEntryChildren":[{"IOCalloutDevice":"/dev/cu.usbmodem101"}]}
]}
devices=guard.serial_devices(tree)
assert devices["/dev/cu.usbmodem11401"]=="C3-IDENTITY"
assert devices["/dev/cu.usbmodem101"]!="C3-IDENTITY"
assert guard.serial_devices([tree])==devices
with tempfile.TemporaryDirectory() as directory:
    binding=Path(directory)/"identity.json"
    binding.write_text(json.dumps({"chip":"esp32c3","usb_serial":"C3-IDENTITY"}))
    with patch.object(guard.sys,"platform","darwin"),patch.object(guard.subprocess,"check_output",return_value=plistlib.dumps(tree)):
        assert guard.resolve(binding)=="/dev/cu.usbmodem11401"
        try:
            guard.resolve(binding,"/dev/cu.usbmodem101")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Reused S3 port must be rejected")
        binding.write_text(json.dumps({"chip":"esp32s3","usb_serial":"S3-IDENTITY"}))
        try:
            guard.resolve(binding)
        except RuntimeError:
            pass
        else:
            raise AssertionError("An S3 binding must be rejected")
print("Passed USB identity/port reuse checks")
