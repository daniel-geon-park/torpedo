from __future__ import annotations

import json
import os
import platform
import pwd
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def _scaled(path: Path, divisor: float, digits: int = 1) -> str | None:
    raw = _read_text(path)
    try:
        return f"{int(raw) / divisor:.{digits}f}" if raw is not None else None
    except ValueError:
        return None


def _sanitize_proc_bytes(raw: bytes) -> str:
    out = []
    for byte in raw:
        if byte in (0x00, 0x0A):
            out.append(" ")
        elif 0x20 <= byte <= 0x7E:
            out.append(chr(byte))
        else:
            out.append(f"\\x{byte:02x}")
    return "".join(out).strip()


class PassiveBackend:
    """Read driver-exported status without opening a Tenstorrent device.

    In particular this backend never imports tt-smi, pyluwen, or tt-umd. Those
    libraries retain /dev/tenstorrent handles, which makes a monitor look like
    a device owner in /proc/driver/tenstorrent/*/pids and in tt-smi.
    """

    def __init__(
        self,
        sys_class: str | Path = "/sys/class/tenstorrent",
        proc_driver: str | Path = "/proc/driver/tenstorrent",
        proc: str | Path = "/proc",
    ) -> None:
        self.sys_class = Path(sys_class)
        self.proc_driver = Path(proc_driver)
        self.proc = Path(proc)
        self.device_infos: list[dict[str, Any]] = []
        self.device_processes: list[dict[str, Any]] = []
        self._devices: list[dict[str, Any]] = []
        self.update_telem()

    @staticmethod
    def _device_index(path: Path) -> int | None:
        try:
            return int(path.name.rsplit("!", 1)[1])
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _hwmon(pci_device: Path) -> Path | None:
        try:
            candidates = sorted((pci_device / "hwmon").glob("hwmon*"))
        except OSError:
            return None
        return candidates[0] if candidates else None

    def _read_device(self, path: Path, index: int) -> dict[str, Any]:
        pci_link = path / "device"
        try:
            pci_device = pci_link.resolve(strict=True)
            bus_id = pci_device.name
        except OSError:
            pci_device = pci_link
            bus_id = "N/A"
        hwmon = self._hwmon(pci_device)

        board_type = _read_text(path / "tt_card_type") or "Tenstorrent"
        board_info = {
            "bus_id": bus_id,
            "board_type": board_type,
            "board_id": _read_text(path / "tt_serial") or "N/A",
            "coords": "N/A",
            "dram_status": "N/A",
            "dram_speed": "N/A",
            "pcie_speed": _read_text(pci_device / "current_link_speed") or "N/A",
            "pcie_width": _read_text(pci_device / "current_link_width") or "N/A",
        }
        telemetry = {
            "voltage": _scaled(hwmon / "in0_input", 1000, 2) if hwmon else None,
            "current": _scaled(hwmon / "curr1_input", 1000) if hwmon else None,
            "power": None,
            "board_power": _scaled(hwmon / "power1_input", 1_000_000) if hwmon else None,
            "aiclk": _read_text(path / "tt_aiclk"),
            "asic_temperature": _scaled(hwmon / "temp1_input", 1000) if hwmon else None,
            "fan_speed": _read_text(hwmon / "fan1_input") if hwmon else None,
            "heartbeat": _read_text(path / "tt_heartbeat"),
        }
        firmwares = {
            "cm_fw": _read_text(path / "tt_fw_bundle_ver"),
            "dm_app_fw": _read_text(path / "tt_m3app_fw_ver"),
        }
        return {
            "index": index,
            "board_info": board_info,
            "telemetry": telemetry,
            "firmwares": firmwares,
            "passive_source": "sysfs/hwmon",
        }

    def update_telem(self) -> None:
        devices = []
        try:
            paths = sorted(self.sys_class.glob("tenstorrent!*"))
        except OSError:
            paths = []
        for path in paths:
            index = self._device_index(path)
            if index is not None:
                devices.append(self._read_device(path, index))
        devices.sort(key=lambda device: device["index"])
        if not devices:
            raise RuntimeError("no Tenstorrent devices detected in sysfs")
        self._devices = devices
        self.device_infos = [dict(device["board_info"]) for device in devices]

    def _process(self, pid: int, device: int) -> dict[str, Any]:
        process = self.proc / str(pid)
        try:
            user = pwd.getpwuid(process.stat().st_uid).pw_name
        except (OSError, KeyError):
            user = "?"
        try:
            raw = (process / "cmdline").read_bytes()
        except OSError:
            raw = b""
        if not raw:
            try:
                raw = (process / "comm").read_bytes()
            except OSError:
                raw = b"?"
        return {
            "pid": pid,
            "user": user,
            "cmdline": _sanitize_proc_bytes(raw) or "?",
            "device": device,
        }

    def update_processes(self) -> None:
        seen: set[tuple[int, int]] = set()
        processes = []
        try:
            paths = sorted(self.proc_driver.glob("*/pids"))
        except OSError:
            paths = []
        for path in paths:
            try:
                device = int(path.parent.name)
                lines = path.read_text(encoding="ascii").splitlines()
            except (OSError, UnicodeError, ValueError):
                continue
            for line in lines:
                try:
                    pid = int(line.split()[0])
                except (IndexError, ValueError):
                    continue
                key = (pid, device)
                if key not in seen:
                    seen.add(key)
                    processes.append(self._process(pid, device))
        self.device_processes = sorted(
            processes, key=lambda process: (process["device"], process["pid"])
        )

    def get_logs_json(self) -> str:
        try:
            distro = platform.freedesktop_os_release().get("PRETTY_NAME", "")
        except OSError:
            distro = ""
        document = {
            "host_info": {
                "OS": platform.system(),
                "Distro": distro,
                "Kernel": platform.release(),
                "Hostname": platform.node(),
                "Platform": platform.machine(),
                "Python": platform.python_version(),
                "Memory": "N/A",
                "Driver": _read_text(Path("/sys/module/tenstorrent/version")) or "N/A",
            },
            "host_sw_vers": {"torpedo_backend": "passive-sysfs"},
            "device_info": self._devices,
            "processes": self.device_processes,
        }
        return json.dumps(document, separators=(",", ":"))
