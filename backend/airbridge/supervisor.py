"""Keep the local receiver, C3 relay and vision service running as one stack."""
import argparse
import asyncio
import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import sys
import time

import aiohttp


class Service:
    def __init__(self,name,command,root,env=None):
        self.name,self.command,self.root,self.env=name,command,root,env
        self.process=None;self.reader=None
        log_file=root/".local"/(name+".log")
        descriptor=os.open(log_file,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600);os.close(descriptor)
        self.log=logging.getLogger("stack."+name);self.log.setLevel(logging.INFO)
        for handler in list(self.log.handlers):
            self.log.removeHandler(handler);handler.close()
        self.log.addHandler(RotatingFileHandler(log_file,maxBytes=3*1024*1024,backupCount=2))

    async def start(self):
        self.process=await asyncio.create_subprocess_exec(*self.command,cwd=self.root,env=self.env,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
        self.reader=asyncio.create_task(self.read())

    async def read(self):
        while line:=await self.process.stdout.readline():
            self.log.info(line.decode(errors="replace").rstrip())

    async def stop(self):
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):os.killpg(self.process.pid,signal.SIGTERM)
            try:await asyncio.wait_for(self.process.wait(),5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):os.killpg(self.process.pid,signal.SIGKILL)
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):await self.reader

    @property
    def alive(self):return self.process is not None and self.process.returncode is None


class Stack:
    def __init__(self,root,config):
        self.root,self.config=root,config
        self.services={};self.http=None
        self.health={"started":time.time(),"receiver_restarts":0,"last_error":None,"recovery_attempts":0}
        self.last_probe=self.last_recovery=0

    def save_health(self):
        self.health["pids"]={name:service.process.pid for name,service in self.services.items() if service.alive}
        self.health["updated"]=time.time()
        path=self.root/".local/stack-health.json"
        temporary=path.with_suffix(".tmp");temporary.write_text(json.dumps(self.health));temporary.chmod(0o600);temporary.replace(path)

    async def request(self,port,path,token,data=None):
        headers={"Authorization":"Bearer "+token,"X-AirBridge-Control":"1"}
        async with self.http.request("GET" if data is None else "POST",f"http://127.0.0.1:{port}"+path,
            headers=headers,json=data,timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status!=200:raise RuntimeError(f"local service {port}: HTTP {response.status}")
            return await response.json()

    async def receiver(self):
        for name in ("backend","receiver"):
            if name in self.services:await self.services[name].stop()
        # Wait for the previous listener to relinquish its socket, including
        # a previous in-process UxPlay reset. Keep the same persistent AirPlay key.
        env=os.environ.copy();env["UXPLAY_BINARY"]=str(self.root/self.config["uxplay_binary"])
        receiver=Service("receiver",["bash","scripts/run_uxplay.sh","-vs",
            "jpegenc ! multifilesink location=.local/frame.jpg"],self.root,env)
        self.services["receiver"]=receiver;await receiver.start()
        await asyncio.sleep(2)
        if not receiver.alive:raise RuntimeError("UxPlay exited; see receiver.log")
        # Identity/TXT fields must be resolved afresh; never invent a receiver.
        discover=await asyncio.create_subprocess_exec(sys.executable,"-m","airbridge.discover","--local-only",
            "--name","C3-Upstream",cwd=self.root,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        try:code=await asyncio.wait_for(discover.wait(),15)
        except asyncio.TimeoutError:
            discover.kill();await discover.wait();raise RuntimeError("Bonjour discovery timed out")
        if code:raise RuntimeError("LocalOnly receiver discovery failed")
        backend=Service("backend",[sys.executable,"-m","airbridge.server","--host",self.config["bridge_host"],
            "--config",".local/relay.json","--frame-file",".local/frame.jpg"],self.root)
        self.services["backend"]=backend;await backend.start()
        self.health["receiver_restarts"]+=1
        self.last_recovery=time.time()

    async def maybe_reconnect_phone(self,agent_state):
        if not self.config.get("auto_reconnect") or time.time()-self.last_recovery<45:return
        if not self.health.get("ble"):return
        if self.health["recovery_attempts"]>=3:return
        if time.time()-self.health.get("last_phone_recovery",0)<300:return
        if agent_state.get("job",{} ) and agent_state["job"]["state"]=="running":return
        self.health["last_phone_recovery"]=time.time();self.health["recovery_attempts"]+=1
        cfg=json.loads((self.root/".local/agent.json").read_text())
        if not cfg.get("usb_observer_udid"):return
        prompt=("维护任务：只恢复本机 iPhone 的屏幕镜像连接。当前通过 USB 备用截图观察，操作仍经 C3 蓝牙。"
            "打开控制中心的屏幕镜像，选择 C3 AirBridge，直到显示勾选。可从屏幕最右上角向下滑动进入控制中心，"
            "或点击辅助触控菜单的控制中心。不要选 C3-Upstream，不要发送消息，不改 Wi-Fi、蓝牙或其它手机设置。"
            "连接后退出控制中心回到原页面；看到镜像勾选或画面来源恢复 C3 后完成。若已经连接则直接完成。"
            "若看到锁屏，立即 blocked 停止，不能尝试解锁或在锁屏里继续操作。")
        await self.request(8871,"/api/tasks",cfg["ui_token"],{"prompt":prompt,"max_steps":16})

    async def monitor(self):
        cfg=json.loads((self.root/".local/agent.json").read_text())
        token=(self.root/".local/token").read_text().strip()
        while True:
            await asyncio.sleep(5)
            try:
                if not self.services["receiver"].alive or not self.services["backend"].alive:
                    await self.receiver()
                if not self.services["agent"].alive:
                    with contextlib.suppress(Exception):await self.request(8766,"/hid",token,{"op":"release"})
                    await self.services["agent"].start()
                if "awake" in self.services and not self.services["awake"].alive:await self.services["awake"].start()
                state=await self.request(8766,"/status",token)
                agent_state=await self.request(8871,"/api/status",cfg["ui_token"])
                frame=self.root/".local/frame.jpg"
                age=time.time()-frame.stat().st_mtime if frame.exists() else None
                self.health.update(connected=state.get("connected"),frame_age=age,
                    ble=state.get("device_status",{}).get("ble"),source=agent_state.get("source"))
                active=agent_state.get("job") and agent_state["job"]["state"]=="running"
                if (age is None or age>20) and state.get("connected") and not active:
                    if state.get("channels",0)>=2 and time.time()-self.last_probe>25:
                        self.last_probe=time.time()
                        received_before=state.get("bytes_up",0)
                        if state.get("device_status",{}).get("ble"):
                            await self.request(8766,"/hid",token,{"op":"move","x":-6,"y":-6})
                            await asyncio.sleep(.7)
                            await self.request(8766,"/hid",token,{"op":"move","x":6,"y":6})
                            await asyncio.sleep(4)
                        new_age=time.time()-frame.stat().st_mtime if frame.exists() else 9999
                        current=await self.request(8766,"/status",token)
                        arriving=current.get("bytes_up",0)-received_before
                        # No image on a static page is ambiguous, even after a
                        # pointer poke (iOS may omit the overlay). Only reset a
                        # stuck decoder when substantial media keeps arriving.
                        if new_age>15 and arriving>128*1024 and time.time()-self.last_recovery>90:
                            self.health["last_error"]="video failed active refresh; restarted receiver"
                            await self.receiver()
                        elif new_age>15:
                            self.health["last_error"]="waiting for visible screen change; static frame alone does not trigger restart"
                    elif state.get("channels",0)<2:
                        await self.maybe_reconnect_phone(agent_state)
                else:
                    self.health["last_error"]=None
                self.save_health()
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.health["last_error"]=str(exc)[:200];self.save_health()
                await asyncio.sleep(10)

    async def run(self):
        self.http=aiohttp.ClientSession()
        try:
            await self.receiver()
            agent=Service("agent",[sys.executable,"-m","airbridge.agent"],self.root)
            self.services["agent"]=agent;await agent.start()
            if sys.platform=="darwin":
                self.services["awake"]=Service("awake",["/usr/bin/caffeinate","-i","-w",str(os.getpid())],self.root)
                await self.services["awake"].start()
            self.save_health()
            await self.monitor()
        finally:
            with contextlib.suppress(Exception):
                await self.request(8766,"/hid",(self.root/".local/token").read_text().strip(),{"op":"release"})
            for name in ("agent","backend","receiver","awake"):
                if name in self.services:await self.services[name].stop()
            await self.http.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",default=".")
    parser.add_argument("--config",default=".local/stack.json")
    args=parser.parse_args();root=Path(args.root).resolve();os.chdir(root)
    config=json.loads(Path(args.config).read_text())
    (root/".local").mkdir(mode=0o700,exist_ok=True)
    async def run():
        stack=Stack(root,config);task=asyncio.create_task(stack.run())
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,task.cancel)
        with contextlib.suppress(asyncio.CancelledError):await task
    asyncio.run(run())


if __name__=="__main__":main()
