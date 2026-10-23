import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw

from airbridge.agent import Agent, Bridge, Pointer, ascii_commands, create_app, cursor_candidates, key_command, validate_action


def jpeg(circle=None):
    image=Image.new("RGB",(720,1560),(20,20,20))
    if circle:
        x,y=circle
        ImageDraw.Draw(image).ellipse((x-16,y-16,x+16,y+16),fill=(80,80,80))
    buf=io.BytesIO();image.save(buf,"JPEG",quality=92);return buf.getvalue()


class ActionTests(unittest.TestCase):
    def test_typing_validates_all_before_any_side_effect(self):
        for text in ["hi\nSEND","你好","a\t", "x"*161,""]:
            with self.subTest(text=text),self.assertRaises(ValueError):ascii_commands(text)
        commands=ascii_commands("A1+a")
        self.assertEqual(commands[0],{"op":"key","usage":4,"modifiers":2})
        self.assertEqual(commands[2]["modifiers"],2)

    def test_action_limits_and_unknown_actions(self):
        for action in [{"action":"click","x":float("nan"),"y":.5},
                       {"action":"click","x":720,"y":1560},
                       {"action":"key","key":"DELETE_ACCOUNT"},
                       {"action":"key","key":"A","modifiers":["ROOT"]},
                       {"action":"scroll","wheel":100},
                       {"action":"shell","command":"anything"},
                       {"action":"swipe","x":.5,"y":.5,"end_x":2,"end_y":.1}]:
            with self.subTest(action=action),self.assertRaises(ValueError):validate_action(action)
        self.assertEqual(key_command("SPACE",["CMD"])["modifiers"],8)

    def test_pointer_detects_transient_circle_without_static_menu(self):
        points=cursor_candidates(jpeg(),jpeg((305,705)))
        self.assertEqual(len(points),1)
        self.assertLess(abs(points[0][0]-305),2)
        self.assertLess(abs(points[0][1]-705),2)
        self.assertFalse(cursor_candidates(jpeg((305,705)),jpeg((305,705))))

    def test_pointer_does_not_chase_disappearing_old_circle(self):
        points=cursor_candidates(jpeg((100,100)),jpeg((400,800)))
        self.assertEqual(len(points),1)
        self.assertLess(abs(points[0][0]-400),2)


class FakeBridge:
    source="c3"
    def __init__(self):self.commands=[];self.frames=0
    async def frame(self,**kwargs):self.frames+=1;return jpeg()
    async def hid(self,command):self.commands.append(command)


class FakeModel:
    def __init__(self,actions):self.actions=iter(actions)
    async def vision(self,*args,**kwargs):return next(self.actions)
    async def ask(self,*args,**kwargs):return next(self.actions)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.cfg={"trace_file":str(Path(self.directory.name)/"trace.jsonl"),"settle_seconds":0}
    async def asyncTearDown(self):self.directory.cleanup()

    async def test_task_observes_again_after_action_before_completing(self):
        bridge=FakeBridge();model=FakeModel([{"action":"text","text":"12+34"},{"action":"key","key":"EQUAL"},{"action":"done","reason":"46 visible"}])
        agent=Agent(self.cfg,bridge,model);agent.start("calculate",4);await agent.task
        self.assertEqual(agent.job["state"],"done")
        self.assertEqual(bridge.frames,3)
        self.assertEqual(bridge.commands[-1],{"op":"release"})
        self.assertEqual(Path(self.cfg["trace_file"]).stat().st_mode & 0o777,0o600)

    async def test_cancel_releases_inputs_and_does_not_continue(self):
        pending=asyncio.Event()
        class SlowModel:
            async def vision(self,*a,**kw):pending.set();await asyncio.Event().wait()
        bridge=FakeBridge();agent=Agent(self.cfg,bridge,SlowModel());agent.start("wait")
        await pending.wait();await agent.stop()
        self.assertEqual(agent.job["state"],"stopped")
        self.assertTrue(all(x=={"op":"release"} for x in bridge.commands))

    async def test_invalid_model_output_cannot_become_input(self):
        bridge=FakeBridge();agent=Agent(self.cfg,bridge,FakeModel([{"action":"shell"},{"action":"blocked","reason":"no tool"}]))
        agent.start("task",2);await agent.task
        self.assertEqual(agent.job["state"],"blocked")
        self.assertEqual(bridge.commands,[{"op":"release"}])

    async def test_cycle_guard_still_observes_completion_after_third_pair(self):
        actions=[{"action":"key","key":"A"},{"action":"key","key":"B"}]*3
        bridge=FakeBridge();agent=Agent(self.cfg,bridge,FakeModel(actions+[{"action":"done","reason":"result visible"}]))
        agent.start("task",8);await agent.task
        self.assertEqual(agent.job["state"],"done")
        self.assertEqual(bridge.frames,7)

    async def test_cycle_guard_prevents_fourth_pair_and_releases_inputs(self):
        actions=[{"action":"key","key":"A"},{"action":"key","key":"B"}]*4
        bridge=FakeBridge();agent=Agent(self.cfg,bridge,FakeModel(actions))
        agent.start("task",8);await agent.task
        self.assertEqual(agent.job["state"],"blocked")
        self.assertIn("重复两步",agent.job["result"])
        self.assertEqual(len(bridge.commands),7)
        self.assertEqual(bridge.commands[-1],{"op":"release"})

    async def test_stop_also_cancels_manual_pointer_operation(self):
        bridge=FakeBridge();agent=Agent(self.cfg,bridge,FakeModel([]));entered=asyncio.Event()
        async def waiting(action):entered.set();await asyncio.Event().wait()
        agent.execute=waiting
        task=asyncio.create_task(agent.manual({"action":"click","x":.5,"y":.5}))
        await entered.wait();await agent.stop()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(bridge.commands[-1],{"op":"release"})

    async def test_no_cursor_means_no_click(self):
        bridge=FakeBridge();model=FakeModel([{"visible":False}]);pointer=Pointer(bridge,model)
        original=asyncio.sleep
        async def immediate(seconds):await original(0)
        with patch("airbridge.agent.asyncio.sleep",immediate),self.assertRaises(RuntimeError):await pointer.click(.5,.5)
        self.assertFalse(any(c["op"]=="click" for c in bridge.commands))

    async def test_stale_video_is_poked_but_never_relabelled(self):
        calls=[]
        async def frame(request):
            calls.append("frame")
            if "move" in calls:return web.Response(body=jpeg(),content_type="image/jpeg")
            return web.Response(status=400)
        async def status(request):return web.json_response({"connected":True,"device_status":{"ble":True}})
        async def hid(request):
            command=await request.json()
            self.assertIn(command,[{"op":"move","x":-6,"y":-6},{"op":"move","x":6,"y":6}]);calls.append("move")
            return web.json_response({"ok":True})
        app=web.Application();app.add_routes([web.get('/frame',frame),web.get('/status',status),web.post('/hid',hid)])
        async with TestClient(TestServer(app)) as client:
            token=Path(self.directory.name)/"token";token.write_text("x"*32)
            bridge=Bridge({"bridge_url":str(client.make_url('/')).rstrip('/'),"token_file":str(token)},client.session)
            self.assertTrue((await bridge.frame()).startswith(b"\xff\xd8"))
            self.assertEqual(calls.count("move"),2)

    async def test_preview_keeps_original_capture_age(self):
        async def unavailable(request):return web.Response(status=503)
        app=web.Application();app.router.add_get('/frame',unavailable)
        async with TestClient(TestServer(app)) as client:
            token=Path(self.directory.name)/"token";token.write_text("x"*32)
            bridge=Bridge({"bridge_url":str(client.make_url('/')).rstrip('/'),"token_file":str(token)},client.session)
            bridge.last_frame=jpeg();bridge.source="usb";captured=time.time()-60;bridge.last_frame_at=captured
            self.assertEqual(await bridge.frame(refresh=False),bridge.last_frame)
            self.assertEqual(bridge.last_frame_at,captured)
            self.assertEqual(bridge.source,"usb")

    async def test_explicit_refresh_cannot_reuse_usb_image_before_a_click(self):
        captures=[]
        async def unavailable(request):return web.Response(status=503)
        async def status(request):return web.json_response({"channels":0})
        app=web.Application();app.add_routes([web.get('/frame',unavailable),web.get('/status',status)])
        async with TestClient(TestServer(app)) as client:
            token=Path(self.directory.name)/"token";token.write_text("x"*32)
            bridge=Bridge({"bridge_url":str(client.make_url('/')).rstrip('/'),"token_file":str(token),"usb_observer_udid":"test"},client.session)
            def capture():captures.append(True);return jpeg()
            bridge.usb_screenshot=capture
            await bridge.frame();await bridge.frame()
            self.assertEqual(len(captures),2)

    async def test_console_requires_auth_and_rejects_cross_origin_control(self):
        token=Path(self.directory.name)/"token";token.write_text("x"*32)
        config={"token_file":str(token),"api_key":"test-only","base_url":"https://example.invalid/v1", "ui_token":"test-ui-token", "ui_bootstrap":"bootstrap"}
        async with TestClient(TestServer(await create_app(config))) as client:
            self.assertEqual((await client.get('/api/status')).status,401)
            headers={"Authorization":"Bearer test-ui-token"}
            self.assertEqual((await client.post('/api/tasks',json={"prompt":"do something"},headers=headers)).status,403)
            headers.update({"X-AirBridge-Control":"1","Origin":"https://untrusted.example"})
            self.assertEqual((await client.post('/api/tasks',json={"prompt":"do something"},headers=headers)).status,403)
            login=await client.get('/open/bootstrap',allow_redirects=False)
            self.assertEqual(login.status,302)
            self.assertTrue(login.cookies['c3_session']['httponly'])
            self.assertEqual(login.cookies['c3_session']['samesite'],'Strict')


if __name__=="__main__":unittest.main()
