#!/usr/bin/env python3
"""Install this repo's local macOS services without publishing credentials."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-host",required=True)
    parser.add_argument("--uxplay-binary",required=True)
    parser.add_argument("--api-key-file",required=True)
    parser.add_argument("--base-url",default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--model",default="qwen3.5-plus")
    parser.add_argument("--usb-observer-udid")
    parser.add_argument("--auto-reconnect",action="store_true",help="Opt in to bounded phone UI recovery through the configured USB observer")
    args=parser.parse_args()
    if sys.platform!="darwin":raise SystemExit("This installer uses macOS LaunchAgents. See docs/deployment.md for Linux.")
    source_root=Path(__file__).resolve().parents[1]
    # launchd services must not depend on a Desktop/Documents checkout or its
    # editable venv (macOS privacy prompts can block interpreter startup).
    root=Path.home()/"Library/Application Support/C3AirBridge";root.mkdir(mode=0o700,parents=True,exist_ok=True)
    local=root/".local";local.mkdir(mode=0o700,exist_ok=True);local.chmod(0o700)
    label="com.yang.c3-airbridge";domain="gui/"+str(os.getuid())
    subprocess.run(["launchctl","bootout",domain+"/"+label],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for name in ("token","uxplay.pem"):
        origin=source_root/".local"/name
        if origin.exists() and not (local/name).exists():shutil.copy2(origin,local/name);(local/name).chmod(0o600)
    for folder in ("scripts","config"):(root/folder).mkdir(exist_ok=True)
    shutil.copy2(source_root/"scripts/run_uxplay.sh",root/"scripts/run_uxplay.sh")
    shutil.copy2(source_root/"config/relay.example.json",root/"config/relay.example.json")
    python=root/".venv/bin/python"
    if not python.exists():subprocess.run([sys.executable,"-m","venv",str(root/".venv")],check=True)
    install_log=local/"install.log"
    with install_log.open("w") as log:
        subprocess.run([str(python),"-m","pip","install",str(source_root)],stdout=log,stderr=log,check=True)
        if args.usb_observer_udid:
            subprocess.run([str(python),"-m","pip","install","pymobiledevice3==11.43.1"],stdout=log,stderr=log,check=True)
    install_log.chmod(0o600)
    api_key=Path(args.api_key_file).read_text().strip()
    if len(api_key)<20:raise SystemExit("API key file is empty or invalid")
    def save(path,data):path.write_text(json.dumps(data,indent=2)+"\n");path.chmod(0o600)
    path=local/"agent.json"
    original=source_root/".local/agent.json"
    config=json.loads(path.read_text()) if path.exists() else (json.loads(original.read_text()) if original.exists() else {})
    config.update(api_key=api_key,base_url=args.base_url,model=args.model,
        token_file=str(local/"token"),bridge_url="http://127.0.0.1:8766")
    config.setdefault("ui_token",secrets.token_urlsafe(32));config.setdefault("ui_bootstrap",secrets.token_urlsafe(24))
    config.pop("pointer_debug_dir",None)
    if args.usb_observer_udid:config["usb_observer_udid"]=args.usb_observer_udid
    save(path,config)
    binary=local/"bin/uxplay";binary.parent.mkdir(exist_ok=True)
    if Path(args.uxplay_binary).resolve()!=binary.resolve():shutil.copy2(args.uxplay_binary,binary)
    binary.chmod(0o700)
    save(local/"stack.json",{"bridge_host":args.bridge_host,"uxplay_binary":str(binary),"auto_reconnect":bool(args.auto_reconnect and args.usb_observer_udid)})
    plist=Path.home()/"Library/LaunchAgents"/(label+".plist");plist.parent.mkdir(parents=True,exist_ok=True)
    # Only take over known old processes from THIS repo's working directory.
    # Never kill an unrelated app merely because it occupies a desired port.
    for port in (8871,8766,17000):
        result=subprocess.run(["lsof","-t",f"-iTCP:{port}","-sTCP:LISTEN"],capture_output=True,text=True)
        for text in set(result.stdout.split()):
            pid=int(text)
            command=subprocess.check_output(["ps","-p",text,"-o","command="],text=True)
            cwd=subprocess.check_output(["lsof","-a","-p",text,"-d","cwd","-Fn"],text=True)
            if not any("n"+str(path) in cwd for path in (root,source_root)) or not any(s in command for s in ("airbridge.agent","c3-airbridge","uxplay")):
                raise SystemExit(f"Port {port} belongs to an unrelated process; leaving it untouched")
            os.kill(pid,signal.SIGTERM)
    time.sleep(2)
    settings={"Label":label,"ProgramArguments":[str(python),"-m","airbridge.supervisor","--root",str(root)],
        "WorkingDirectory":str(root),"RunAtLoad":True,"KeepAlive":True,"ThrottleInterval":15,"ExitTimeOut":30,
        "EnvironmentVariables":{"PATH":"/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin","PYTHONUNBUFFERED":"1"},
        "StandardOutPath":str(local/"stack-console.log"),"StandardErrorPath":str(local/"stack-console.log")}
    plist.write_bytes(plistlib.dumps(settings));plist.chmod(0o600)
    subprocess.run(["launchctl","bootstrap",domain,str(plist)],check=True)
    launcher=Path.home()/"Desktop/C3 AirBridge.command"
    launcher.write_text("#!/bin/zsh\ncd "+shlex.quote(str(root))+"\nlaunchctl kickstart "+shlex.quote(domain+"/"+label)+" 2>/dev/null\n"+
        "if [[ -f .local/agent-url ]]; then\n  agent_url=\"$(<.local/agent-url)\"\n  open \"$agent_url\"\nelse\n  print '服务正在启动，请稍后再打开。'\nfi\n")
    launcher.chmod(0o700)
    print("Installed:",plist)
    print("Open:",launcher)
    print("Runtime:",root)
    print("API key saved only in private .local/agent.json; services bind to loopback except the LAN C3 relay.")


if __name__=="__main__":main()
