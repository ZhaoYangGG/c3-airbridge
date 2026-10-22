#!/usr/bin/env python3
"""Exercise real firmware with backend --echo. This is NOT an AirPlay test."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="C3 LAN IPv4 or mDNS hostname")
    parser.add_argument("--port", type=int, default=7100)
    parser.add_argument("--bytes", type=int, default=1024 * 1024)
    parser.add_argument("--token-file", default=".local/token")
    args = parser.parse_args()
    if args.bytes < 1:
        raise SystemExit("bytes must be positive")
    sent, received = hashlib.sha256(), hashlib.sha256()
    started = time.monotonic()
    with socket.create_connection((args.device, args.port), timeout=10) as sock:
        remaining = args.bytes
        while remaining:
            block = os.urandom(min(4096, remaining))
            sock.sendall(block)
            got = bytearray()
            while len(got) < len(block):
                data = sock.recv(len(block) - len(got))
                if not data:
                    raise SystemExit("C3 closed before echo completed")
                got.extend(data)
            sent.update(block)
            received.update(got)
            remaining -= len(block)
    seconds = time.monotonic() - started
    token = Path(args.token_file).read_text().strip()
    request = Request("http://127.0.0.1:8766/status", headers={"Authorization": "Bearer " + token})
    with urlopen(request, timeout=5) as response:
        status = json.load(response)
    result = {"kind": "real-c3-transport-echo", "airplay_test": False, "bytes": args.bytes,
              "sha256_match": sent.digest() == received.digest(), "seconds": round(seconds, 3),
              "roundtrip_mbit_s": round(args.bytes * 16 / seconds / 1e6, 3),
              "device_status": status["device_status"]}
    print(json.dumps(result, indent=2))
    if sent.digest() != received.digest():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
