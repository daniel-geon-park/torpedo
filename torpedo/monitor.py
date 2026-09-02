from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from torpedo.memory import physical_dram_bytes, read_memory_stats

LOG = logging.getLogger(__name__)


def create_tt_smi_backend(backend: str = "luwen") -> Any:
    """Discover devices once and retain the resulting hardware context."""
    from tt_smi.backend import TTSMIBackend

    if backend == "umd":
        from tt_smi import constants
        from tt_umd import TopologyDiscovery

        descriptor, devices = TopologyDiscovery.discover(
            options=constants.get_default_discovery_options()
        )
    elif backend == "luwen":
        from tt_tools_common.utils_common.tools_utils import detect_chips_with_callback

        descriptor = None
        devices = dict(
            enumerate(detect_chips_with_callback(print_status=False))
        )
    else:
        raise ValueError(f"unknown backend: {backend}")
    if not devices:
        raise RuntimeError("no Tenstorrent devices detected")
    return TTSMIBackend(
        devices=devices,
        umd_cluster_descriptor=descriptor,
        pretty_output=False,
    )


class DeviceMonitor:
    def __init__(
        self,
        interval: float = 1.0,
        backend_factory: Callable[[], Any] = create_tt_smi_backend,
        recovery_max_interval: float = 30.0,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        if recovery_max_interval <= 0:
            raise ValueError("recovery_max_interval must be positive")
        self.interval = interval
        self.recovery_max_interval = max(interval, recovery_max_interval)
        self.backend_factory = backend_factory
        self._backend: Any | None = None
        self._snapshot: dict[str, Any] = self._empty_snapshot("initializing")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _empty_snapshot(self, state: str, error: str | None = None) -> dict[str, Any]:
        return {
            "state": state,
            "sampled_at": self._now(),
            "sample_interval_seconds": self.interval,
            "devices": [],
            "processes": [],
            "error": error,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="tt-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(5.0, self.interval * 2))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _publish(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = value

    def _publish_recovering(self, exc: Exception) -> None:
        previous = self.snapshot()
        value = self._empty_snapshot("recovering", str(exc))
        value["last_refresh_attempt_at"] = value["sampled_at"]
        last_successful_sample_at = previous.get("last_successful_sample_at")
        if previous.get("state") == "ready":
            last_successful_sample_at = previous.get("sampled_at")
        if last_successful_sample_at is not None:
            value["last_successful_sample_at"] = last_successful_sample_at
        self._publish(value)

    def _run(self) -> None:
        retry_interval = self.interval
        recovering = False
        while not self._stop.is_set():
            initializing = self._backend is None
            try:
                if initializing:
                    self._backend = self.backend_factory()
                    self._refresh(initial=True)
                else:
                    self._refresh(initial=False)
            except Exception as exc:
                phase = "context initialization" if initializing else "telemetry refresh"
                LOG.exception(
                    "Tenstorrent %s failed; rediscovering in %.1fs",
                    phase,
                    retry_interval,
                )
                # A device reset invalidates the PciChip/TTDevice objects held by
                # TTSMIBackend. Drop the complete context before rediscovery.
                self._backend = None
                self._publish_recovering(exc)
                recovering = True
                if self._stop.wait(retry_interval):
                    return
                retry_interval = min(retry_interval * 2, self.recovery_max_interval)
                continue

            if recovering:
                LOG.info("Tenstorrent context rediscovered; telemetry recovered")
                recovering = False
            retry_interval = self.interval
            if self._stop.wait(self.interval):
                return

    def _refresh(self, initial: bool) -> None:
        assert self._backend is not None
        if not initial:
            self._backend.update_telem()
        self._backend.update_processes()

        # get_logs_json mutates board_type when adding the local/remote suffix.
        # Preserve the backend's static data so repeated samples remain stable.
        device_infos = copy.deepcopy(self._backend.device_infos)
        try:
            raw = json.loads(self._backend.get_logs_json())
        finally:
            self._backend.device_infos = device_infos

        all_processes = raw.get("processes", [])
        monitor_pid = os.getpid()
        monitor_handles = sorted(
            {int(process["device"]) for process in all_processes if process["pid"] == monitor_pid}
        )
        processes = [process for process in all_processes if process["pid"] != monitor_pid]
        by_device: dict[int, list[dict[str, Any]]] = {}
        memory_by_device = read_memory_stats()
        for process in processes:
            by_device.setdefault(int(process["device"]), []).append(process)

        devices = []
        for index, info in enumerate(raw.get("device_info", [])):
            holders = by_device.get(index, [])
            memory = memory_by_device.get(index)
            memory_total = physical_dram_bytes(info)
            # tt-telemetry defines a missing allocator region as zero: tt-metal
            # creates the region on first use of a device.
            memory_used = memory.dram_used_bytes if memory else 0
            for process in holders:
                process["memory_usage_bytes"] = (
                    memory.process_dram_bytes.get(process["pid"])
                    if memory
                    else None
                )
            devices.append(
                {
                    "index": index,
                    "held": bool(holders),
                    "processes": holders,
                    # Current tt-smi/driver APIs expose health telemetry and open
                    # handles, but not compute occupancy or allocated DRAM bytes.
                    "usage_percent": None,
                    "memory_usage_bytes": memory_used,
                    "memory_total_bytes": memory_total,
                    "memory_usage_percent": (
                        round(memory_used * 100 / memory_total, 2)
                        if memory_used is not None and memory_total
                        else None
                    ),
                    "metric_availability": {
                        "usage_percent": "not exposed by tt-smi 6.3.0",
                        "memory_usage_bytes": (
                            "tt-metal shared-memory allocator v3"
                            if memory
                            else "no allocator region (device has not been used by compatible tt-metal)"
                        ),
                        "memory_total_bytes": (
                            "enabled Blackhole GDDR channels × 4 GiB"
                            if memory_total is not None
                            else "capacity unavailable for this architecture"
                        ),
                    },
                    **info,
                }
            )

        self._publish(
            {
                "state": "ready",
                "sampled_at": self._now(),
                "sample_interval_seconds": self.interval,
                "devices": devices,
                "processes": processes,
                "monitor_device_handles": monitor_handles,
                "host": raw.get("host_info", {}),
                "software": raw.get("host_sw_vers", {}),
                "error": None,
            }
        )
