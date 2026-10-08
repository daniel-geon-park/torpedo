from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from torpedo.monitor import DeviceMonitor


class StatusServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], monitor: DeviceMonitor):
        super().__init__(address, StatusHandler)
        self.monitor = monitor


class StatusHandler(BaseHTTPRequestHandler):
    server: StatusServer

    def do_GET(self) -> None:
        path = self.path.partition("?")[0]
        if path in ("/", "/v1/devices"):
            self._json(200, self.server.monitor.snapshot())
        elif path == "/healthz":
            snapshot = self.server.monitor.snapshot()
            status = 200 if snapshot["state"] == "ready" else 503
            self._json(status, {"state": snapshot["state"], "error": snapshot["error"]})
        else:
            self._json(404, {"error": "not found"})

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.getLogger("torpedo.http").info(fmt, *args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent Tenstorrent status server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    monitor = DeviceMonitor(interval=args.interval)
    monitor.start()
    server = StatusServer((args.host, args.port), monitor)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        monitor.stop()


if __name__ == "__main__":
    main()
