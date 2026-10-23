#!/usr/bin/env python3
"""Optional local setup diagnostics. Runtime phone tasks use BLE GATT instead."""
import argparse
import json
import os
import re
import select
import termios
import time
from pathlib import Path
from device_guard import DEFAULT_IDENTITY, resolve


def receive(fd, pattern, timeout=4):
    end = time.monotonic() + timeout
    data = b""
    while time.monotonic() < end:
        if select.select([fd], [], [], 0.1)[0]:
            try:
                data += os.read(fd, 16384)
            except BlockingIOError:
                continue
            match = re.search(pattern, data)
            if match:
                return json.loads(match[1])
    raise TimeoutError("Board did not acknowledge the diagnostic command")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--commands", default="[]", help="JSON array of explicit HID commands")
    args = parser.parse_args()
    commands = json.loads(args.commands)
    if not isinstance(commands, list) or len(commands) > 80:
        raise ValueError("Expected at most 80 commands")
    port = resolve(args.identity, args.port)
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        config = termios.tcgetattr(fd)
        config[0] = config[1] = config[3] = 0
        config[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        config[4] = config[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, config)
        os.write(fd, b"\nstatus\n")
        state = receive(fd, rb"C3_AGENT_STATUS (\{[^\r\n]+\})")
        if state.get("target") != "esp32c3":
            raise RuntimeError("Connected firmware does not identify as ESP32-C3")
        print(json.dumps(state), flush=True)
        if commands and not state.get("ble"):
            raise RuntimeError("Secure iPhone HID connection is not ready")
        for command in commands:
            if not isinstance(command, dict):
                raise ValueError("Command must be an object")
            packet = json.dumps(command, separators=(",", ":")).encode()
            if len(packet) > 250:
                raise ValueError("Command too long")
            os.write(fd, b"hid " + packet + b"\n")
            ack = receive(fd, rb"C3_AGENT_HID (\{[^\r\n]+\})")
            if not ack.get("ok"):
                raise RuntimeError("HID command rejected")
            print(json.dumps(ack), flush=True)
            time.sleep(0.15)
    finally:
        # Always attempt to release any mouse/keyboard state after diagnostics.
        if commands:
            os.write(fd, b'hid {"op":"release"}\n')
            try:
                receive(fd, rb"C3_AGENT_HID (\{[^\r\n]+\})", 1)
            except TimeoutError:
                pass
        os.close(fd)


if __name__ == "__main__":
    main()
