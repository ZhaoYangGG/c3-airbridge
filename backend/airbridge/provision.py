"""Provision Wi-Fi over USB without putting credentials in source or shell history."""
import argparse
import getpass
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs
import serial


def token_file(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("x") as file:
            os.chmod(path, 0o600)
            file.write(secrets.token_hex(32) + "\n")
    return path.read_text().strip()


def configure(port, ssid, password, host, backend_port, token):
    if not 0 < len(ssid.encode()) <= 32 or len(password.encode()) > 64:
        raise ValueError("SSID/password length invalid")
    ipaddress.IPv4Address(host)  # first version deliberately uses an explicit LAN IPv4
    if len(token) < 24 or not 1 <= backend_port <= 65535:
        raise ValueError("invalid backend configuration")
    payload = json.dumps({"ssid": ssid, "password": password, "host": host,
                          "port": backend_port, "token": token}, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) + 12 >= 1024:
        raise ValueError("configuration too large")
    device = serial.Serial()
    device.port, device.baudrate, device.timeout = port, 115200, 0.2
    device.dtr = device.rts = False
    device.open()
    try:
        time.sleep(1.5)
        device.reset_input_buffer()
        device.write(b"configure " + payload + b"\n")
        received = bytearray()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            received.extend(device.read(1024))
            if b"AIRBRIDGE_CONFIG_OK" in received:
                return
            if b"AIRBRIDGE_CONFIG_ERROR" in received:
                raise ValueError("device rejected configuration")
        raise TimeoutError("no firmware acknowledgement; check port and flashed firmware")
    finally:
        device.close()


def web(args, token):
    # Random path plus same-origin checks prevent unrelated browser pages from
    # configuring the USB device through a loopback form submission.
    nonce = secrets.token_urlsafe(24)
    origin = f"http://127.0.0.1:{args.web_port}"
    route = "/" + nonce
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # never log credentials, request bodies, or nonce paths

        def do_GET(self):
            if self.path != route or self.headers.get("Host") != f"127.0.0.1:{args.web_port}":
                self.send_error(404)
                return
            import html
            body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>C3 AirBridge 配网</title>
<style>body{{font:16px system-ui;max-width:520px;margin:60px auto;padding:20px}}label{{display:block;margin:20px 0}}input{{display:block;width:95%;padding:10px;margin-top:8px;font:inherit}}button{{padding:12px;font:inherit}}</style>
<h1>C3 AirBridge 配网</h1><p>密码仅通过本机 USB 写入开发板，不提交到 GitHub。</p>
<form method="post" action="{route}">
<label>2.4 GHz Wi-Fi 名称<input name="ssid" required maxlength="32" autocomplete="off"></label>
<label>Wi-Fi 密码<input name="password" type="password" maxlength="64" autocomplete="off"></label>
<label>这台 Mac 的局域网 IPv4<input name="host" value="{html.escape(args.host)}" required></label>
<button>保存到 C3 并连接后端</button></form></html>'''.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != route or self.headers.get("Origin") != origin or self.headers.get("Host") != f"127.0.0.1:{args.web_port}":
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2048:
                    raise ValueError("invalid input size")
                data = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True)
                configure(args.port, data["ssid"][0], data.get("password", [""])[0],
                          data["host"][0], args.backend_port, token)
                message = "配置已写入。C3 正在重启并连接 Wi-Fi；可以关闭此页面。"
                status = 200
            except (ValueError, KeyError, TimeoutError, serial.SerialException):
                message, status = "配置失败：检查 Wi-Fi 名称、Mac IPv4 和 USB 连接后重试。", 400
            body = message.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", args.web_port), Handler)
    server.timeout = 1
    print(f"Local provisioning: {origin}{route}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="USB serial device")
    parser.add_argument("--host", required=True, help="Mac/backend LAN IPv4")
    parser.add_argument("--backend-port", type=int, default=8765)
    parser.add_argument("--token-file", default=".local/token")
    parser.add_argument("--web", action="store_true")
    parser.add_argument("--web-port", type=int, default=8870)
    args = parser.parse_args()
    token = token_file(args.token_file)
    if args.web:
        web(args, token)
    else:
        configure(args.port, input("2.4 GHz Wi-Fi SSID: "), getpass.getpass("Wi-Fi password: "),
                  args.host, args.backend_port, token)
        print("Configuration saved; board restarting.")


if __name__ == "__main__":
    main()
