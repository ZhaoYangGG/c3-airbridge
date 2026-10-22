#!/usr/bin/env python3
"""Exercise real firmware with backend --echo. This is NOT an AirPlay test."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="C3 LAN IPv4 or mDNS hostname")
    parser.add_argument("--port", type=int, default=7100)
    parser.add_argument("--bytes", type=int, default=1024 * 1024)
    parser.add_argument("--token-file", default=".local/token")
    parser.add_argument("--stream", action="store_true", help="concurrent send/receive for throughput; default measures chunk round trips")
    args = parser.parse_args()
    if args.bytes < 1:
        raise SystemExit("bytes must be positive")
    sent, received = hashlib.sha256(), hashlib.sha256()
    started = time.monotonic()
    with socket.create_connection((args.device, args.port), timeout=10) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        remaining = args.bytes
        failures = []
        def receive_stream():
            try:
                left = args.bytes
                while left:
                    data = sock.recv(min(65536, left))
                    if not data:
                        raise RuntimeError("C3 closed before echo completed")
                    received.update(data)
                    left -= len(data)
            except Exception as exc:
                failures.append(exc)
        receiver = threading.Thread(target=receive_stream) if args.stream else None
        if receiver:
            receiver.start()
        while remaining:
            block = os.urandom(min(4096, remaining))
            sock.sendall(block)
            sent.update(block)
            if not receiver:
                got = bytearray()
                while len(got) < len(block):
                    data = sock.recv(len(block) - len(got))
                    if not data:
                        raise SystemExit("C3 closed before echo completed")
                    got.extend(data)
                received.update(got)
            remaining -= len(block)
        if receiver:
            receiver.join(15)
            if receiver.is_alive():
                sock.shutdown(socket.SHUT_RDWR)
                receiver.join()
                raise SystemExit("stream timed out")
            if failures:
                raise failures[0]
    seconds = time.monotonic() - started
    token = Path(args.token_file).read_text().strip()
    request = Request("http://127.0.0.1:8766/status", headers={"Authorization": "Bearer " + token})
    with urlopen(request, timeout=5) as response:
        status = json.load(response)
    result = {"kind": "real-c3-transport-echo", "airplay_test": False, "bytes": args.bytes,
              "mode": "stream" if args.stream else "chunk-roundtrip",
              "sha256_match": sent.digest() == received.digest(), "seconds": round(seconds, 3),
              "roundtrip_mbit_s": round(args.bytes * 16 / seconds / 1e6, 3),
              "device_status": status["device_status"]}
    print(json.dumps(result, indent=2))
    if sent.digest() != received.digest():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
