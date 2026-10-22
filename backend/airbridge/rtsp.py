"""Bounded RTSP/HTTP framing for AirPlay control. Video bytes never pass here.

Only cleartext SETUP binary plists are edited. Encrypted control sessions and
chunked bodies are deliberately rejected instead of silently corrupting them.
"""
from dataclasses import dataclass
import plistlib

MAX_HEADER = 16 * 1024
MAX_BODY = 512 * 1024


@dataclass
class Message:
    start: str
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name, default=""):
        return next((v for k, v in self.headers if k.lower() == name.lower()), default)

    def pack(self):
        headers = [(k, v) for k, v in self.headers if k.lower() != "content-length"]
        headers.append(("Content-Length", str(len(self.body))))
        return (self.start + "\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers)
                + "\r\n").encode("latin-1") + self.body


class Parser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > MAX_BODY + MAX_HEADER:
            raise ValueError("control buffer limit exceeded")
        result = []
        while self.buffer:
            end = self.buffer.find(b"\r\n\r\n")
            if end < 0:
                if len(self.buffer) > MAX_HEADER:
                    raise ValueError("header too large")
                break
            if end > MAX_HEADER:
                raise ValueError("header too large")
            lines = self.buffer[:end].decode("latin-1").split("\r\n")
            if not lines[0].startswith(("RTSP/1.0 ", "HTTP/1.1 ", "HTTP/1.0 ",
                                         "GET ", "POST ", "PUT ", "SETUP ", "OPTIONS ",
                                         "RECORD ", "SET_PARAMETER ", "GET_PARAMETER ",
                                         "FLUSH ", "TEARDOWN ", "ANNOUNCE ", "PAUSE ")):
                raise ValueError("unsupported/encrypted control stream")
            headers = []
            for line in lines[1:]:
                if ":" not in line:
                    raise ValueError("malformed header")
                k, v = line.split(":", 1)
                headers.append((k, v.strip()))
            lengths = [v for k, v in headers if k.lower() == "content-length"]
            if len(lengths) > 1:
                raise ValueError("duplicate content length")
            if any(k.lower() == "transfer-encoding" for k, _ in headers):
                raise ValueError("chunked control not supported")
            size = int(lengths[0]) if lengths else 0
            if not 0 <= size <= MAX_BODY:
                raise ValueError("body too large")
            total = end + 4 + size
            if len(self.buffer) < total:
                break
            result.append(Message(lines[0], headers, bytes(self.buffer[end + 4:total])))
            del self.buffer[:total]
        return result


def rewrite_ports(body, mapper):
    """Map explicit AirPlay ports; never scan/replace arbitrary binary bytes."""
    if not body.startswith((b"bplist00", b"<?xml")):
        return body
    root = plistlib.loads(body)
    changed = False

    def walk(obj, stream_type=None):
        nonlocal changed
        if isinstance(obj, dict):
            stream_type = obj.get("type", stream_type)
            for key, value in list(obj.items()):
                if key in {"timingPort", "eventPort", "dataPort", "controlPort"} and type(value) is int and value:
                    replacement = mapper(key, value, stream_type)
                    if replacement != value:
                        obj[key], changed = replacement, True
                else:
                    walk(value, stream_type)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, stream_type)

    walk(root)
    return plistlib.dumps(root, fmt=plistlib.FMT_BINARY) if changed else body
