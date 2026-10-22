"""Wire protocol shared by the ESP32 and Python peers. All integers are big endian."""
import asyncio
from enum import IntEnum
import json
import struct

HEADER = struct.Struct("!BBHI")  # version, kind, channel, payload length
VERSION = 1
MAX_PAYLOAD = 4096
MAX_CHANNELS = 8


class Kind(IntEnum):
    HELLO = 1
    CONFIG = 2
    OPEN = 3
    DATA = 4
    CLOSE = 5
    PING = 6
    PONG = 7
    HID = 8
    STATUS = 9
    UDP_OPEN = 10
    ERROR = 11
    ACK = 12


def encode(kind, channel=0, payload=b""):
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":")).encode()
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("frame exceeds 4096 bytes")
    if not 0 <= channel <= 65535:
        raise ValueError("invalid channel")
    return HEADER.pack(VERSION, kind, channel, len(payload)) + payload


async def read_frame(reader):
    version, kind, channel, size = HEADER.unpack(await reader.readexactly(HEADER.size))
    if version != VERSION or size > MAX_PAYLOAD:
        raise ValueError("unsupported version or oversized frame")
    return Kind(kind), channel, await reader.readexactly(size)


class Peer:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.lock = asyncio.Lock()
        self.writer.transport.set_write_buffer_limits(high=16384, low=4096)

    async def send(self, kind, channel=0, payload=b""):
        async with self.lock:
            self.writer.write(encode(kind, channel, payload))
            await asyncio.wait_for(self.writer.drain(), 10)
