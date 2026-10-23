import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from airbridge.supervisor import Stack


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_static_frame(self, arriving):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.local').mkdir()
            (root/'.local/agent.json').write_text(json.dumps({'ui_token':'test'}))
            (root/'.local/token').write_text('test')
            frame=root/'.local/frame.jpg';frame.write_bytes(b'old');os.utime(frame,(time.time()-100,time.time()-100))
            class FakeStack(Stack):
                checks=0
                async def request(self,port,path,token,data=None):
                    if path=='/api/status':return {'job':None,'source':'c3'}
                    if path=='/status':
                        self.checks+=1
                        return {'connected':True,'channels':3,'device_status':{'ble':True},
                                'bytes_up':0 if self.checks==1 else arriving}
                    return {'ok':True}
                async def receiver(self):self.health['receiver_restarts']+=1
            stack=FakeStack(root,{})
            stack.services={name:SimpleNamespace(alive=True,process=SimpleNamespace(pid=123)) for name in ('receiver','backend','agent')}
            stack.last_recovery=time.time()-120
            iterations=0
            async def tick(delay):
                nonlocal iterations
                if delay==5:
                    iterations+=1
                    if iterations>1:raise asyncio.CancelledError
            with patch('airbridge.supervisor.asyncio.sleep',tick),self.assertRaises(asyncio.CancelledError):
                await stack.monitor()
            return stack.health

    async def test_static_screen_without_media_does_not_restart_receiver(self):
        health=await self.exercise_static_frame(0)
        self.assertEqual(health['receiver_restarts'],0)
        self.assertIn('static frame alone',health['last_error'])

    async def test_stale_decoder_with_substantial_arriving_media_can_restart(self):
        health=await self.exercise_static_frame(256*1024)
        self.assertEqual(health['receiver_restarts'],1)


if __name__=='__main__':unittest.main()
