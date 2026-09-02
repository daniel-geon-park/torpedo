import os
import threading
import time

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


def wait_until(predicate, timeout=1):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.005)
    assert predicate()


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
        wait_until(lambda: monitor.snapshot()["state"] == "ready")
        snapshot = monitor.snapshot()
        assert calls == 1
        assert snapshot["devices"][0]["held"] is True
        assert snapshot["devices"][0]["processes"][0]["pid"] == 42
        assert snapshot["devices"][0]["usage_percent"] is None
    finally:
        monitor.stop()


def test_initialization_failure_is_retried():
    calls = 0
    backend = FakeBackend()

    def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("broken")
        return backend

    monitor = DeviceMonitor(
        interval=0.01,
        recovery_max_interval=0.02,
        backend_factory=factory,
    )
    monitor.start()
    try:
        wait_until(lambda: calls >= 2 and monitor.snapshot()["state"] == "ready")
        assert monitor.snapshot()["error"] is None
    finally:
        monitor.stop()


def test_refresh_failure_discards_stale_backend_and_recovers():
    class ResetBackend(FakeBackend):
        def update_telem(self):
            raise RuntimeError("Ioctl failed with ENODEV")

    calls = 0
    stale_backend = ResetBackend()
    recovered_backend = FakeBackend()
    recovery_attempted = threading.Event()
    allow_recovery = threading.Event()

    def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            return stale_backend
        recovery_attempted.set()
        allow_recovery.wait(timeout=1)
        return recovered_backend

    monitor = DeviceMonitor(
        interval=0.01,
        recovery_max_interval=0.02,
        backend_factory=factory,
    )
    monitor.start()
    try:
        wait_until(lambda: recovery_attempted.is_set())
        recovering = monitor.snapshot()
        assert recovering["state"] == "recovering"
        assert recovering["devices"] == []
        assert recovering["error"] == "Ioctl failed with ENODEV"
        assert "last_successful_sample_at" in recovering

        allow_recovery.set()
        wait_until(
            lambda: calls >= 2
            and recovered_backend.refreshes >= 1
            and monitor.snapshot()["state"] == "ready"
        )
        snapshot = monitor.snapshot()
        assert snapshot["error"] is None
        assert snapshot["devices"][0]["telemetry"]["aiclk"] == 1000
    finally:
        allow_recovery.set()
        monitor.stop()
