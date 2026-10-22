import asyncio
import hashlib
import json
import os
import plistlib
from pathlib import Path
import tempfile
import unittest

from airbridge.protocol import HEADER, Kind, MAX_PAYLOAD, Peer, encode, read_frame
from airbridge.rtsp import Message, Parser, rewrite_ports, MAX_BODY
from airbridge.server import Backend, DatagramQueue


class FramingTests(unittest.TestCase):
    def test_limit(self):
        with self.assertRaises(ValueError):
            encode(Kind.DATA, 1, b"x" * (MAX_PAYLOAD + 1))

    def test_fragmented_binary_plist_and_pipelining(self):
        body = plistlib.dumps({"timingPort": 12345}, fmt=plistlib.FMT_BINARY)
        first = Message("SETUP rtsp://phone RTSP/1.0", [("CSeq", "1")], body)
        second = Message("OPTIONS * RTSP/1.0", [], b"")
        parser = Parser()
        result = []
        for byte in first.pack() + second.pack():
            result.extend(parser.feed(bytes([byte])))
        self.assertEqual(len(result), 2)
        self.assertEqual(plistlib.loads(result[0].body)["timingPort"], 12345)
        self.assertFalse(parser.buffer)

    def test_reject_bad_control_lengths(self):
        for header in (f"Content-Length: {MAX_BODY + 1}", "Content-Length: -1",
                       "Content-Length: 1\r\nContent-Length: 2", "Transfer-Encoding: chunked"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                Parser().feed(f"POST / RTSP/1.0\r\n{header}\r\n\r\n".encode())

    def test_map_plist_structure_without_touching_keys(self):
        original = {"timingPort": 16000, "streams": [{"type": 110, "dataPort": 17100}],
                    "ekey": bytes(range(64)), "opaque": "17100"}
        result = plistlib.loads(rewrite_ports(plistlib.dumps(original, fmt=plistlib.FMT_BINARY),
            lambda key, value, kind: 7100 if kind == 110 else 6000))
        self.assertEqual(result["streams"][0]["dataPort"], 7100)
        self.assertEqual(result["timingPort"], 6000)
        self.assertEqual(result["ekey"], original["ekey"])
        self.assertEqual(result["opaque"], "17100")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = "test-token-" + "x" * 32
        self.config = {"listeners": [{"id": 1, "proto": "tcp", "port": 7000, "upstream": 17000}], "mdns": []}
        self.backend = Backend(self.token, self.config, echo=True)
        self.server = await asyncio.start_server(self.backend.accept, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.writers = []

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        for _ in range(50):
            if self.backend.session is None:
                break
            await asyncio.sleep(0.01)

    async def connect(self, token=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.append(writer)
        peer = Peer(reader, writer)
        await peer.send(Kind.HELLO, payload={"device": "simulator", "token": token or self.token})
        return peer

    async def receive(self, peer):
        return await asyncio.wait_for(read_frame(peer.reader), 2)

    async def test_authentication(self):
        peer = await self.connect("wrong")
        self.assertEqual(await asyncio.wait_for(peer.reader.read(), 2), b"")
        self.assertIsNone(self.backend.session)

    async def test_oversized_frame_disconnects_before_payload(self):
        peer = await self.connect()
        await self.receive(peer)
        peer.writer.write(HEADER.pack(1, Kind.DATA, 1, MAX_PAYLOAD + 1))
        await peer.writer.drain()
        self.assertEqual(await asyncio.wait_for(peer.reader.read(), 2), b"")

    async def test_megabyte_opaque_stream_and_heartbeat(self):
        peer = await self.connect()
        self.assertEqual((await self.receive(peer))[0], Kind.CONFIG)
        await peer.send(Kind.OPEN, 1, {"listener": 1, "peer": "192.0.2.3", "port": 40000})
        self.assertTrue(json.loads((await self.receive(peer))[2])["ok"])
        sent, received = hashlib.sha256(), hashlib.sha256()
        for i in range(256):
            payload = os.urandom(MAX_PAYLOAD)
            sent.update(payload)
            await peer.send(Kind.DATA, 1, payload)
            kind, channel, data = await self.receive(peer)
            self.assertEqual((kind, channel), (Kind.DATA, 1))
            received.update(data)
        await peer.send(Kind.PING, payload=b"alive")
        self.assertEqual(await self.receive(peer), (Kind.PONG, 0, b"alive"))
        self.assertEqual(sent.digest(), received.digest())
        self.assertEqual(self.backend.session.bytes_up, 1048576)
        self.assertEqual(self.backend.session.bytes_down, 1048576)

    async def test_eight_channels_and_ninth_rejected(self):
        peer = await self.connect()
        await self.receive(peer)
        for channel in range(1, 9):
            await peer.send(Kind.OPEN, channel, {"listener": 1, "peer": "192.0.2.3", "port": 40000})
            self.assertTrue(json.loads((await self.receive(peer))[2])["ok"])
        await peer.send(Kind.OPEN, 9, {"listener": 1, "peer": "192.0.2.3", "port": 40000})
        self.assertFalse(json.loads((await self.receive(peer))[2])["ok"])
        self.assertEqual((await self.receive(peer))[:2], (Kind.CLOSE, 9))
        self.assertEqual(len(self.backend.session.channels), 8)

    async def test_rtsp_reply_port_mapping(self):
        peer = await self.connect()
        await self.receive(peer)
        session = self.backend.session
        session.services += [{"id": 2, "proto": "tcp", "port": 7100, "upstream": 17100},
                             {"id": 3, "proto": "udp", "port": 6000, "upstream": 16000}]
        message = Message("RTSP/1.0 200 OK", [], plistlib.dumps({
            "timingPort": 16000, "eventPort": 0, "streams": [{"type": 110, "dataPort": 17100}]},
            fmt=plistlib.FMT_BINARY))
        result = Parser().feed(session.control_response(message))[0]
        body = plistlib.loads(result.body)
        self.assertEqual(body["timingPort"], 6000)
        self.assertEqual(body["streams"][0]["dataPort"], 7100)

    async def test_close_echo_channel_is_acknowledged(self):
        peer = await self.connect()
        await self.receive(peer)
        await peer.send(Kind.OPEN, 1, {"listener": 1, "peer": "192.0.2.3", "port": 40000})
        await self.receive(peer)
        await peer.send(Kind.CLOSE, 1)
        self.assertEqual((await self.receive(peer))[:2], (Kind.CLOSE, 1))
        self.assertFalse(self.backend.session.channels)

    async def test_authenticated_device_can_reconnect(self):
        old = await self.connect()
        await self.receive(old)
        new = await self.connect()
        self.assertEqual((await self.receive(new))[0], Kind.CONFIG)
        self.assertEqual(await asyncio.wait_for(old.reader.read(), 2), b"")
        await new.send(Kind.PING, payload=b"new-session")
        self.assertEqual(await self.receive(new), (Kind.PONG, 0, b"new-session"))

    async def test_inflight_data_after_close_keeps_tunnel_alive(self):
        peer = await self.connect()
        await self.receive(peer)
        await peer.send(Kind.OPEN, 1, {"listener": 1, "peer": "192.0.2.3", "port": 40000})
        await self.receive(peer)
        await peer.send(Kind.CLOSE, 1)
        await self.receive(peer)
        await peer.send(Kind.DATA, 1, b"already in flight")
        await peer.send(Kind.PING, payload=b"still alive")
        self.assertEqual(await self.receive(peer), (Kind.PONG, 0, b"still alive"))
        await peer.send(Kind.DATA, 99, b"never opened")
        self.assertEqual(await asyncio.wait_for(peer.reader.read(), 2), b"")

    async def test_burst_video_does_not_starve_loopback_writer(self):
        payload = os.urandom(1024 * 1024)
        completed = asyncio.get_running_loop().create_future()
        async def sink(reader, writer):
            try:
                received = await reader.readexactly(len(payload))
                completed.set_result(hashlib.sha256(received).digest())
            except Exception as exc:
                if not completed.done(): completed.set_exception(exc)
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(sink, '127.0.0.1', 0)
        try:
            self.backend.echo = False
            self.config['listeners'][0]['upstream'] = server.sockets[0].getsockname()[1]
            peer = await self.connect()
            await self.receive(peer)
            await peer.send(Kind.OPEN, 1, {"listener": 1, "peer": "192.0.2.3", "port": 40000})
            await self.receive(peer)
            for offset in range(0, len(payload), MAX_PAYLOAD):
                await peer.send(Kind.DATA, 1, payload[offset:offset + MAX_PAYLOAD])
            self.assertEqual(await asyncio.wait_for(completed, 5), hashlib.sha256(payload).digest())
        finally:
            server.close()
            await server.wait_closed()

    async def test_info_probe_close_does_not_close_active_mirror(self):
        peer = await self.connect()
        await self.receive(peer)
        for channel in (1, 2, 3):
            await peer.send(Kind.OPEN, channel, {"listener": 1, "peer": "192.0.2.3", "port": 40000+channel})
            await self.receive(peer)
        session = self.backend.session
        session.channels[1].update(peer='192.0.2.3', parser=Parser(), owns_media=True)
        session.channels[2].update(peer='192.0.2.3')
        session.channels[3].update(peer='192.0.2.3', parser=Parser())
        await peer.send(Kind.CLOSE, 3)
        self.assertEqual((await self.receive(peer))[:2], (Kind.CLOSE, 3))
        await peer.send(Kind.DATA, 2, b'video after info probe')
        self.assertEqual(await self.receive(peer), (Kind.DATA, 2, b'video after info probe'))
        self.assertIn(1, session.channels)
        await peer.send(Kind.CLOSE, 1)
        # Closing the actual SETUP owner still releases its media channels.
        replies = [await self.receive(peer), await self.receive(peer)]
        self.assertEqual({(r[0],r[1]) for r in replies}, {(Kind.CLOSE,1),(Kind.CLOSE,2)})
        self.assertFalse(session.channels)

    async def test_frame_api_requires_auth_and_rejects_stale_images(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "frame.jpg"
            image = b"\xff\xd8test-frame\xff\xd9"
            file.write_bytes(image)
            self.backend.frame_file = file
            server = await asyncio.start_server(self.backend.admin, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async def get(token):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(f"GET /frame HTTP/1.1\r\nAuthorization: Bearer {token}\r\n\r\n".encode())
                await writer.drain()
                result = await asyncio.wait_for(reader.read(), 2)
                writer.close()
                await writer.wait_closed()
                return result
            try:
                self.assertIn(b"401", (await get("wrong")).split(b"\r\n")[0])
                result = await get(self.token)
                self.assertIn(b"200", result.split(b"\r\n")[0])
                self.assertEqual(result.split(b"\r\n\r\n", 1)[1], image)
                os.utime(file, (1, 1))
                self.assertNotIn(b"200", (await get(self.token)).split(b"\r\n")[0])
            finally:
                server.close()
                await server.wait_closed()

    async def test_reverse_timing_datagrams(self):
        peer = await self.connect()
        await self.receive(peer)
        task = asyncio.create_task(self.backend.session.reverse_udp("192.0.2.3", 5555))
        kind, channel, payload = await self.receive(peer)
        self.assertEqual(kind, Kind.UDP_OPEN)
        self.assertEqual(json.loads(payload)["port"], 5555)
        await peer.send(Kind.ACK, channel, {"ok": True})
        mapped_port = await task
        transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
            DatagramQueue, remote_addr=("127.0.0.1", mapped_port))
        try:
            transport.sendto(b"timing-request")
            self.assertEqual(await self.receive(peer), (Kind.DATA, channel, b"timing-request"))
            await peer.send(Kind.DATA, channel, b"timing-response")
            data, _ = await asyncio.wait_for(protocol.queue.get(), 2)
            self.assertEqual(data, b"timing-response")
        finally:
            transport.close()


if __name__ == "__main__":
    unittest.main()
