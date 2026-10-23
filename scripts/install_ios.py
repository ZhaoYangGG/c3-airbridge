#!/usr/bin/env python3
"""Build/install C3 Agent; optionally import a key into the app's Keychain."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device",required=True,help="Physical iPhone UDID from xcrun devicectl list devices")
    parser.add_argument("--team",required=True,help="Your Xcode signing Team ID")
    parser.add_argument("--bundle-id",default="com.zhaoyang.c3screenprobe")
    parser.add_argument("--key-file",type=Path,help="Optional private text file containing an API key")
    parser.add_argument("--base-url",default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--model",default="qwen3.5-plus")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    local=root/".local";local.mkdir(mode=0o700,exist_ok=True);local.chmod(0o700)
    with (local/"install-ios.log").open("w") as log:
        def run(command):subprocess.run(command,cwd=root,stdout=log,stderr=log,check=True)
        run(["xcodebuild","-project","ios/C3Agent.xcodeproj","-scheme","C3Agent","-configuration","Debug",
             "-destination","generic/platform=iOS","-derivedDataPath",str(local/"ios-build"),"-allowProvisioningUpdates",
             "DEVELOPMENT_TEAM="+args.team,"PRODUCT_BUNDLE_IDENTIFIER="+args.bundle_id,"build"])
        prefix=["xcrun","devicectl","device"]
        run(prefix+["install","app","--device",args.device,"--timeout","45",str(local/"ios-build/Build/Products/Debug-iphoneos/C3Agent.app")])
        if args.key_file:
            secret=args.key_file.read_text().strip()
            if not secret or len(secret)>4096:raise ValueError("Key file is empty or invalid")
            path=local/"bootstrap.json"
            path.write_text(json.dumps({"api_key":secret,"base_url":args.base_url,"model":args.model}));path.chmod(0o600)
            try:
                run(prefix+["copy","to","--device",args.device,"--timeout","20","--domain-type","appDataContainer",
                            "--domain-identifier",args.bundle_id,"--source",str(path),"--destination","Documents/bootstrap.json"])
            finally:path.unlink(missing_ok=True)
        run(prefix+["process","launch","--device",args.device,"--timeout","25",args.bundle_id])
    (local/"install-ios.log").chmod(0o600)
    print("Installed C3 Agent. Complete Bluetooth permission/pairing and screen-sharing consent on the phone.")


if __name__=="__main__":main()
