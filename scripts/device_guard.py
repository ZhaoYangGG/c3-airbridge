"""Resolve a locally bound C3 by USB identity, without opening serial ports."""
import json
import plistlib
import subprocess
import sys
from pathlib import Path

DEFAULT_IDENTITY = Path(__file__).resolve().parents[1] / ".local/c3-device.json"


def serial_devices(tree):
    found = {}
    def visit(node, serial=None):
        if not isinstance(node, dict):
            return
        serial = node.get("USB Serial Number", serial)
        port = node.get("IOCalloutDevice")
        if port and serial:
            found[port] = serial.upper()
        for child in node.get("IORegistryEntryChildren", []):
            visit(child, serial)
    for node in tree if isinstance(tree, list) else [tree]:
        visit(node)
    return found


def resolve(identity=DEFAULT_IDENTITY, port=None):
    if sys.platform != "darwin":
        raise RuntimeError("This guarded development helper currently requires macOS IOKit")
    binding = json.loads(Path(identity).read_text())
    expected = binding.get("usb_serial", "").upper()
    if binding.get("chip") != "esp32c3" or not expected:
        raise RuntimeError("Identity file must bind an independently verified ESP32-C3 USB serial")
    tree = plistlib.loads(subprocess.check_output(["ioreg", "-a", "-l", "-p", "IOService"]))
    devices = serial_devices(tree)
    matches = [p for p, serial in devices.items() if serial == expected]
    if len(matches) != 1 or (port is not None and port != matches[0]):
        raise RuntimeError("C3 USB identity/port mismatch. No serial port was opened. Recheck the board; never reuse a remembered port name.")
    return matches[0]


if __name__ == "__main__":
    print(resolve())
