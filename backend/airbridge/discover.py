"""Copy real UxPlay discovery metadata; do not invent AirPlay identities/keys."""
import argparse
import json
from pathlib import Path
import threading
import ctypes
import select
import socket
import sys
import time
from types import SimpleNamespace
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf


class Listener(ServiceListener):
    def __init__(self, match):
        self.match, self.records = match, {}
        self.lock = threading.Lock()

    def add_service(self, zc, type_, name):
        if self.match.lower() not in name.lower():
            return
        info = zc.get_service_info(type_, name, timeout=2000)
        if info:
            with self.lock:
                self.records[type_] = info

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name):
        pass


def resolve_local(name, type_, timeout):
    """Read macOS LocalOnly Bonjour records without publishing on the LAN."""
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_int32, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_uint16, ctypes.c_uint16, ctypes.c_void_p, ctypes.c_void_p)
    result = []

    @callback_type
    def callback(ref, flags, interface, error, fullname, host, port, length, txt, context):
        if error:
            result.append(RuntimeError(f'Bonjour resolve error {error}'))
            return
        raw = ctypes.string_at(txt, length)
        properties, offset = {}, 0
        while offset < len(raw):
            size = raw[offset]
            entry = raw[offset + 1:offset + 1 + size]
            key, _, value = entry.partition(b'=')
            properties[key] = value
            offset += size + 1
        result.append(SimpleNamespace(name=name + '.' + type_,
            port=socket.ntohs(port), properties=properties))

    ref = ctypes.c_void_p()
    lib.DNSServiceResolve.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        callback_type, ctypes.c_void_p]
    lib.DNSServiceRefSockFD.argtypes = [ctypes.c_void_p]
    lib.DNSServiceProcessResult.argtypes = [ctypes.c_void_p]
    lib.DNSServiceRefDeallocate.argtypes = [ctypes.c_void_p]
    error = lib.DNSServiceResolve(ctypes.byref(ref), 0, 0xffffffff,
        name.encode(), type_.removesuffix('.local.').encode(), b'local.', callback, None)
    if error:
        raise RuntimeError(f'Bonjour resolve start error {error}')
    try:
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            if select.select([lib.DNSServiceRefSockFD(ref)], [], [], max(0, deadline-time.monotonic()))[0]:
                error = lib.DNSServiceProcessResult(ref)
                if error:
                    raise RuntimeError(f'Bonjour processing error {error}')
        if not result:
            raise TimeoutError(f'LocalOnly receiver not found: {name}')
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]
    finally:
        lib.DNSServiceRefDeallocate(ref)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="C3-Upstream")
    parser.add_argument("--template", default="config/relay.example.json")
    parser.add_argument("--output", default=".local/relay.json")
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--local-only", action="store_true", help="macOS patched UxPlay local Bonjour records")
    args = parser.parse_args()
    listener = Listener(args.name)
    zc = None
    browsers = []
    if args.local_only:
        if sys.platform != 'darwin':
            raise SystemExit('--local-only requires macOS Bonjour')
        airplay = resolve_local(args.name, '_airplay._tcp.local.', args.seconds)
        mac = airplay.properties[b'deviceid'].decode().replace(':', '').upper()
        listener.records = {'_airplay._tcp.local.': airplay,
            '_raop._tcp.local.': resolve_local(mac + '@' + args.name, '_raop._tcp.local.', args.seconds)}
    else:
        zc = Zeroconf()
        browsers = [ServiceBrowser(zc, t, listener) for t in ("_airplay._tcp.local.", "_raop._tcp.local.")]
    try:
        if not args.local_only:
            threading.Event().wait(args.seconds)
        if len(listener.records) != 2:
            raise SystemExit("Both UxPlay AirPlay/RAOP records were not found. Start UxPlay first.")
        config = json.loads(Path(args.template).read_text())
        config["mdns"] = []
        for type_, info in listener.records.items():
            if info.port != 17000:
                raise SystemExit(f"Unexpected upstream control port {info.port}; expected 17000")
            instance = info.name.removesuffix("." + type_)
            if type_.startswith("_raop"):
                prefix = instance.split("@", 1)[0]
                instance = prefix + "@C3 AirBridge"
            else:
                instance = "C3 AirBridge"
            txt = {k.decode("ascii"): (v or b"").decode("utf-8") for k, v in info.properties.items()}
            config["mdns"].append({"type": type_.split(".")[0], "instance": instance,
                                    "port": 7000, "txt": txt})
        from .protocol import encode, Kind
        encode(Kind.CONFIG, payload=config)  # must fit on the C3
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(config, indent=2) + "\n")
        print(f"Saved two real receiver records to {output}; C3 advertises 'C3 AirBridge'.")
    finally:
        for browser in browsers:
            browser.cancel()
        if zc:
            zc.close()


if __name__ == "__main__":
    main()
