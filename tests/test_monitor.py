import time
import os

from torpedo.monitor import DeviceMonitor


class FakeBackend:
    def __init__(self):
        self.device_infos = [{"board_type": "p150a"}]
        self.device_processes = []
        self.refreshes = 0

    def update_telem(self):
        self.refreshes += 1

    def update_processes(self):
        self.device_processes = [{"pid": 42, "user": "test", "device": 0, "cmdline": "job"}]

    def get_logs_json(self):
        return '''{"host_info":{"Hostname":"test"},"host_sw_vers":{},
        "device_info":[{"board_info":{"board_type":"p150a"},"telemetry":{"aiclk":1000}}],
        "processes":[{"pid":42,"user":"test","device":0,"cmdline":"job"},
        {"pid":%d,"user":"test","device":0,"cmdline":"torpedo"}]}''' % os.getpid()


def test_initializes_once_and_publishes_device_status():
    calls = 0
    backend = FakeBackend()

    def factory():
        nonlocal calls
        calls += 1
        return backend

    monitor = DeviceMonitor(interval=0.01, backend_factory=factory)
    monitor.start()
    try:
        deadline = time.time() + 1
        while monitor.snapshot()["state"] != "ready" and time.time() < deadline:
            time.sleep(0.005)
        snapshot = monitor.snapshot()
        assert calls == 1
        assert snapshot["devices"][0]["held"] is True
        assert snapshot["devices"][0]["processes"][0]["pid"] == 42
        assert snapshot["devices"][0]["usage_percent"] is None
    finally:
        monitor.stop()


def test_initialization_failure_is_reported():
    def factory():
        raise RuntimeError("broken")

    monitor = DeviceMonitor(interval=1, backend_factory=factory)
    monitor.start()
    monitor._thread.join(timeout=1)
    assert monitor.snapshot()["state"] == "initialization_error"
    assert monitor.snapshot()["error"] == "broken"
