"""Copy real UxPlay discovery metadata; do not invent AirPlay identities/keys."""
import argparse
import json
from pathlib import Path
import threading
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="C3-Upstream")
    parser.add_argument("--template", default="config/relay.example.json")
    parser.add_argument("--output", default=".local/relay.json")
    parser.add_argument("--seconds", type=float, default=6)
    args = parser.parse_args()
    listener = Listener(args.name)
    zc = Zeroconf()
    browsers = [ServiceBrowser(zc, t, listener) for t in ("_airplay._tcp.local.", "_raop._tcp.local.")]
    try:
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
        zc.close()


if __name__ == "__main__":
    main()
