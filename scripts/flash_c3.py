#!/usr/bin/env python3
"""Build and flash only the locally bound ESP32-C3, preserving NVS/bonds."""
import argparse
import subprocess
from pathlib import Path
from device_guard import DEFAULT_IDENTITY, resolve

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
parser.add_argument("--port", help="Optional extra assertion; a mismatch aborts before opening serial")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
resolve(args.identity, args.port)
subprocess.run(["idf.py", "-C", str(root / "firmware"), "build"], check=True)
port = resolve(args.identity, args.port)
config = (root / "firmware/sdkconfig").read_text()
if 'CONFIG_IDF_TARGET="esp32c3"' not in config:
    raise RuntimeError("Firmware target is not ESP32-C3; refusing to flash")
subprocess.run(["idf.py", "-C", str(root / "firmware"), "-p", port, "flash"], check=True)
