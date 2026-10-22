"""Single-device, bounded TCP/UDP tunnel. Run on the same host as UxPlay.

Experimental LAN transport: the token authenticates a device, but traffic is not
encrypted. Never expose this listener directly to the public Internet.
"""
import argparse
import asyncio
import contextlib
from collections import deque
import hmac
import ipaddress
import json
import logging
from pathlib import Path
import socket
import time

from .protocol import Kind, Peer, MAX_CHANNELS, MAX_PAYLOAD, read_frame
from .rtsp import Parser, rewrite_ports

LOG = logging.getLogger("airbridge")


class DatagramQueue(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue = asyncio.Queue(8)
        self.drops = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) > MAX_PAYLOAD:
            self.drops += 1
            return
        try:
            self.queue.put_nowait((data, addr))
        except asyncio.QueueFull:
            self.drops += 1

    def error_received(self, exc):
        LOG.warning("UDP error: %s", exc)


class Session:
    def __init__(self, peer, services, echo=False):
        self.peer, self.services, self.echo = peer, services, echo
        self.channels = {}
        self.opening = set()
        self.closed = deque(maxlen=MAX_CHANNELS * 2)
        self.tasks = set()
        self.reverse = {}
        self.acks = {}
        self.next_reverse = 32768
        self.last_status = {}
        self.started = time.time()
        self.bytes_up = self.bytes_down = 0
        self.device = "unknown"
        self.finished = asyncio.Event()
        self.stopping = False

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def chunks(self, channel, payload):
        for offset in range(0, len(payload), MAX_PAYLOAD):
            await self.peer.send(Kind.DATA, channel, payload[offset:offset + MAX_PAYLOAD])
        self.bytes_down += len(payload)

    async def udp_pump(self, channel, protocol, remote=None):
        try:
            while True:
                data, addr = await protocol.queue.get()
                # Save only the local UxPlay endpoint; datagrams stay bounded.
                self.channels[channel]["reply"] = addr
                await self.chunks(channel, data)
        except (ConnectionError, asyncio.TimeoutError):
            pass

    async def reverse_udp(self, peer_ip, port):
        key = (peer_ip, port)
        if key in self.reverse:
            return self.reverse[key]
        if len(self.channels) + len(self.opening) >= MAX_CHANNELS:
            raise ValueError("channel limit reached")
        channel = self.next_reverse
        self.next_reverse += 1
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(DatagramQueue, local_addr=("127.0.0.1", 0))
        self.channels[channel] = {"transport": transport, "protocol": protocol, "udp": True, "peer": peer_ip}
        try:
            await self.request(Kind.UDP_OPEN, channel, {"peer": peer_ip, "port": port})
        except BaseException:
            transport.close()
            self.channels.pop(channel, None)
            raise
        self.spawn(self.udp_pump(channel, protocol))
        local_port = transport.get_extra_info("sockname")[1]
        self.reverse[key] = local_port
        return local_port

    async def request(self, kind, channel, payload):
        future = asyncio.get_running_loop().create_future()
        if channel in self.acks:
            raise ValueError("request already pending")
        self.acks[channel] = future
        try:
            await self.peer.send(kind, channel, payload)
            result = await asyncio.wait_for(future, 5)
            if not result.get("ok"):
                raise ValueError(result.get("error", "device rejected request"))
            return result
        finally:
            self.acks.pop(channel, None)

    async def control_request(self, message, peer_ip):
        if message.start.startswith("SETUP "):
            # UxPlay sees its proxy peer as 127.0.0.1. Redirect unsolicited
            # timing/resend traffic through a loopback UDP endpoint and tunnel.
            ports = set()
            rewrite_ports(message.body, lambda k, p, t: (ports.add(p) or p)
                          if k in {"timingPort", "controlPort"} else p)
            mapping = {p: await self.reverse_udp(peer_ip, p) for p in ports}
            message.body = rewrite_ports(message.body, lambda k, p, t: mapping.get(p, p)
                                         if k in {"timingPort", "controlPort"} else p)
        return message.pack()

    def control_response(self, message):
        def mapper(key, port, stream_type):
            proto = "tcp" if key == "eventPort" or (key == "dataPort" and stream_type == 110) else "udp"
            for service in self.services:
                if service["proto"] == proto and service["upstream"] == port:
                    return service["port"]
            raise ValueError(f"unmapped negotiated {proto} port {port}; refusing broken session")
        message.body = rewrite_ports(message.body, mapper)
        return message.pack()

    async def open_channel(self, channel, meta):
        if channel == 0 or channel in self.channels or len(self.channels) >= MAX_CHANNELS:
            raise ValueError("invalid channel or channel limit")
        service = next((s for s in self.services if s["id"] == meta["listener"]), None)
        if service is None:
            raise ValueError("unknown listener")
        if channel in self.closed:
            self.closed.remove(channel)
        peer_ip = str(ipaddress.IPv4Address(meta["peer"]))
        if self.echo:
            self.channels[channel] = {"echo": True}
            await self.peer.send(Kind.ACK, channel, {"ok": True})
            return
        if service["proto"] == "udp":
            transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
                DatagramQueue, remote_addr=("127.0.0.1", service["upstream"]))
            self.channels[channel] = {"udp": True, "transport": transport, "protocol": protocol, "peer": peer_ip,
                                      "reply": ("127.0.0.1", service["upstream"])}
            self.spawn(self.udp_pump(channel, protocol))
        else:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(
                "127.0.0.1", service["upstream"], limit=16384), 5)
            writer.transport.set_write_buffer_limits(high=16384, low=4096)
            state = {"writer": writer, "reader": reader, "peer": peer_ip,
                     "parser": Parser() if service.get("control") else None,
                     "queue": asyncio.Queue(8)}
            self.channels[channel] = state
            state["tasks"] = [self.spawn(self.upstream_writer(channel, state)),
                              self.spawn(self.upstream_reader(channel, state))]
        await self.peer.send(Kind.ACK, channel, {"ok": True})

    async def upstream_writer(self, channel, state):
        try:
            while True:
                data = await state["queue"].get()
                if data is None:
                    if state["parser"] and state["parser"].buffer:
                        raise ValueError("truncated control request")
                    if state["writer"].can_write_eof():
                        state["writer"].write_eof()
                    return
                if state["parser"]:
                    for message in state["parser"].feed(data):
                        if message.start.startswith("SETUP "):
                            state["owns_media"] = True
                        state["writer"].write(await self.control_request(message, state["peer"]))
                        await asyncio.wait_for(state["writer"].drain(), 10)
                else:
                    state["writer"].write(data)
                    await asyncio.wait_for(state["writer"].drain(), 10)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            LOG.warning("channel %s write: %s", channel, exc)
            state["writer"].close()
            await self.peer.send(Kind.CLOSE, channel)

    async def upstream_reader(self, channel, state):
        parser = Parser() if state["parser"] else None
        try:
            while data := await state["reader"].read(MAX_PAYLOAD):
                if parser:
                    for message in parser.feed(data):
                        await self.chunks(channel, self.control_response(message))
                else:
                    await self.chunks(channel, data)
            if parser and parser.buffer:
                raise ValueError("truncated control response")
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            LOG.warning("channel %s read: %s", channel, exc)
        finally:
            await self.peer.send(Kind.CLOSE, channel)
            self.close_channel(channel)

    def close_channel(self, channel):
        state = self.channels.pop(channel, None)
        if state:
            if channel not in self.closed:
                self.closed.append(channel)
            for task in state.get("tasks", []):
                if task is not asyncio.current_task():
                    task.cancel()
            if "writer" in state:
                state["writer"].close()
            if "transport" in state:
                state["transport"].close()
            if state.get("owns_media") and not self.stopping:
                # An AirPlay control connection owns its timing/audio/video
                # channels. Release them when that control session ends.
                for other, value in list(self.channels.items()):
                    if value.get("peer") == state["peer"]:
                        self.close_channel(other)
                        self.spawn(self.peer.send(Kind.CLOSE, other))
                self.reverse = {key: value for key, value in self.reverse.items() if key[0] != state["peer"]}

    async def run(self):
        try:
            while True:
                kind, channel, payload = await asyncio.wait_for(read_frame(self.peer.reader), 45)
                if kind == Kind.PING:
                    await self.peer.send(Kind.PONG, channel, payload)
                elif kind == Kind.STATUS:
                    self.last_status = json.loads(payload)
                elif kind == Kind.ACK:
                    future = self.acks.get(channel)
                    if future and not future.done():
                        future.set_result(json.loads(payload))
                elif kind == Kind.OPEN:
                    # Opening must not block the reader: UDP_OPEN requires ACKs
                    # arriving on the same tunnel while a control task waits.
                    if channel in self.opening or channel in self.channels or len(self.channels) + len(self.opening) >= MAX_CHANNELS:
                        await self.peer.send(Kind.ACK, channel, {"ok": False, "error": "channel unavailable"})
                        await self.peer.send(Kind.CLOSE, channel)
                    else:
                        self.opening.add(channel)
                        self.spawn(self.safe_open(channel, json.loads(payload)))
                elif kind == Kind.DATA:
                    state = self.channels.get(channel)
                    if state is None:
                        # CLOSE and DATA travel in opposite directions. Bytes
                        # already in flight for a recently closed channel are
                        # harmless; an entirely unknown channel is still bad.
                        if channel in self.closed:
                            continue
                        raise ValueError("data on unknown channel")
                    self.bytes_up += len(payload)
                    if state.get("echo"):
                        await self.chunks(channel, payload)
                    elif state.get("udp"):
                        remote = state.get("reply")
                        if remote:
                            if state["transport"].get_extra_info("peername"):
                                state["transport"].sendto(payload)
                            else:
                                state["transport"].sendto(payload, remote)
                    else:
                        try:
                            state["queue"].put_nowait(payload)
                            # Buffered tunnel reads may otherwise monopolize
                            # the loop and overflow even a fast loopback sink.
                            await asyncio.sleep(0)
                        except asyncio.QueueFull:
                            # Fail boundedly rather than deadlock ACK delivery.
                            await self.peer.send(Kind.CLOSE, channel)
                            self.close_channel(channel)
                elif kind == Kind.CLOSE:
                    state = self.channels.get(channel)
                    if state and "queue" in state:
                        try:
                            state["queue"].put_nowait(None)
                        except asyncio.QueueFull:
                            self.close_channel(channel)
                    else:
                        self.close_channel(channel)
                        await self.peer.send(Kind.CLOSE, channel)
                else:
                    raise ValueError(f"unexpected frame {kind}")
        finally:
            self.stopping = True
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for channel in list(self.channels):
                self.close_channel(channel)
            for future in self.acks.values():
                if not future.done():
                    future.set_exception(ConnectionError("device disconnected"))

    async def safe_open(self, channel, meta):
        try:
            await self.open_channel(channel, meta)
        except (OSError, KeyError, ValueError, asyncio.TimeoutError) as exc:
            LOG.warning("open channel %s: %s", channel, exc)
            await self.peer.send(Kind.ACK, channel, {"ok": False, "error": "upstream unavailable"})
            await self.peer.send(Kind.CLOSE, channel)
        finally:
            self.opening.discard(channel)


def load_config(path):
    data = json.loads(Path(path).read_text())
    if len(data.get("listeners", [])) > 6:
        raise ValueError("maximum six listeners")
    ids, bindings = set(), set()
    for item in data["listeners"]:
        if item["proto"] not in {"tcp", "udp"}:
            raise ValueError("invalid listener protocol")
        if not all(type(item[k]) is int and 1 <= item[k] <= 65535 for k in ("id", "port", "upstream")):
            raise ValueError("invalid listener port/id")
        binding = item["proto"], item["port"]
        if item["id"] in ids or binding in bindings:
            raise ValueError("duplicate listener")
        ids.add(item["id"])
        bindings.add(binding)
    return data


class Backend:
    def __init__(self, token, config, echo=False, frame_file=None):
        if len(token) < 24:
            raise ValueError("token must be at least 24 characters")
        self.token, self.config, self.echo = token, config, echo
        self.session = None
        self.frame_file = Path(frame_file) if frame_file else None

    async def accept(self, reader, writer):
        peer = Peer(reader, writer)
        session = None
        try:
            kind, channel, payload = await asyncio.wait_for(read_frame(reader), 5)
            hello = json.loads(payload)
            if kind != Kind.HELLO or channel or not hmac.compare_digest(str(hello.get("token", "")), self.token):
                raise ValueError("authentication failed")
            device = str(hello.get("device", "unknown"))[:80]
            if self.session:
                old = self.session
                if old.device != device:
                    raise ValueError("one device already connected")
                old.peer.writer.close()
                await asyncio.wait_for(old.finished.wait(), 5)
            session = Session(peer, self.config["listeners"], self.echo)
            session.device = device
            self.session = session
            await peer.send(Kind.CONFIG, payload=self.config)
            LOG.info("device connected: %s", session.device)
            await session.run()
        except (OSError, ValueError, KeyError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            LOG.info("device disconnected: %s: %s", type(exc).__name__, exc)
        finally:
            if self.session is session:
                self.session = None
            if session:
                session.finished.set()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def admin(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            if len(raw) > 8192:
                raise ValueError("header too big")
            lines = raw.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split()
            headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            headers = {k.lower(): v.strip() for k, v in headers.items()}
            if not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + self.token):
                status, result = 401, {"error": "unauthorized"}
            elif method == "GET" and path == "/status":
                s = self.session
                status, result = 200, {"connected": s is not None, "device": s.device if s else None,
                    "device_status": s.last_status if s else {}, "channels": len(s.channels) if s else 0,
                    "bytes_up": s.bytes_up if s else 0, "bytes_down": s.bytes_down if s else 0}
            elif method == "POST" and path == "/hid" and self.session:
                size = int(headers.get("content-length", "0"))
                if not 0 < size <= 1024:
                    raise ValueError("invalid body size")
                command = json.loads(await asyncio.wait_for(reader.readexactly(size), 3))
                status, result = 200, await self.session.request(Kind.HID, 0, command)
            elif method == "GET" and path == "/frame" and self.frame_file:
                if time.time() - self.frame_file.stat().st_mtime > 5:
                    raise ValueError("stale frame")
                with self.frame_file.open("rb") as file:
                    image = file.read(2 * 1024 * 1024 + 1)
                if len(image) > 2 * 1024 * 1024 or not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
                    raise ValueError("incomplete frame; retry")
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\nContent-Length: {len(image)}\r\nConnection: close\r\n\r\n".encode() + image)
                await asyncio.wait_for(writer.drain(), 3)
                writer.close()
                return
            else:
                status, result = 404, {"error": "not found or device offline"}
        except (ValueError, KeyError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            status, result = 400, {"error": "invalid request or device timeout"}
        data = json.dumps(result).encode()
        writer.write(f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        with contextlib.suppress(OSError):
            await writer.drain()
        writer.close()


async def serve(args):
    config = load_config(args.config)
    token = Path(args.token_file).read_text().strip()
    backend = Backend(token, config, args.echo, args.frame_file)
    relay = await asyncio.start_server(backend.accept, args.host, args.port, limit=16384)
    admin = await asyncio.start_server(backend.admin, "127.0.0.1", args.admin_port, limit=8192)
    LOG.info("relay %s:%d; local API 127.0.0.1:%d; mode=%s", args.host, args.port, args.admin_port,
             "transport echo test (not AirPlay)" if args.echo else "upstream proxy")
    async with relay, admin:
        await asyncio.gather(relay.serve_forever(), admin.serve_forever())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--admin-port", type=int, default=8766)
    parser.add_argument("--token-file", default=".local/token")
    parser.add_argument("--config", default="config/relay.example.json")
    parser.add_argument("--echo", action="store_true", help="echo transport test; does not receive AirPlay")
    parser.add_argument("--frame-file", help="fresh JPEG written by UxPlay/GStreamer, available at authenticated /frame")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
