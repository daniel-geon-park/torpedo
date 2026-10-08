import json
from pathlib import Path

from torpedo.passive import PassiveBackend


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="ascii")


def test_reads_passive_device_and_process_data(tmp_path):
    sys_class = tmp_path / "sys/class/tenstorrent"
    pci = tmp_path / "sys/devices/pci0000:00/0000:02:00.0"
    device = sys_class / "tenstorrent!0"
    device.mkdir(parents=True)
    pci.mkdir(parents=True)
    (device / "device").symlink_to(pci, target_is_directory=True)

    write(device / "tt_card_type", "p150a\n")
    write(device / "tt_serial", "000004033191A0C7\n")
    write(device / "tt_aiclk", "800\n")
    write(device / "tt_heartbeat", "306\n")
    write(device / "tt_fw_bundle_ver", "19.13.2.0\n")
    write(device / "tt_m3app_fw_ver", "0.29.2.0\n")
    write(pci / "current_link_speed", "32.0 GT/s PCIe\n")
    write(pci / "current_link_width", "16\n")
    hwmon = pci / "hwmon/hwmon2"
    write(hwmon / "name", "blackhole\n")
    write(hwmon / "temp1_input", "65914\n")
    write(hwmon / "power1_input", "41000000\n")
    write(hwmon / "curr1_input", "58000\n")
    write(hwmon / "in0_input", "709\n")
    write(hwmon / "fan1_input", "1655\n")

    proc = tmp_path / "proc"
    write(proc / "42/cmdline", "python\0job.py\0")
    write(tmp_path / "proc-driver/0/pids", "42\n")

    backend = PassiveBackend(sys_class, tmp_path / "proc-driver", proc)
    backend.update_processes()
    document = json.loads(backend.get_logs_json())
    info = document["device_info"][0]

    assert info["index"] == 0
    assert info["board_info"]["board_type"] == "p150a"
    assert info["board_info"]["bus_id"] == "0000:02:00.0"
    assert info["telemetry"]["asic_temperature"] == "65.9"
    assert info["telemetry"]["board_power"] == "41.0"
    assert info["telemetry"]["voltage"] == "0.71"
    assert document["processes"][0]["pid"] == 42
    assert document["processes"][0]["cmdline"] == "python job.py"


def test_raises_when_sysfs_has_no_devices(tmp_path):
    try:
        PassiveBackend(tmp_path / "missing", tmp_path / "proc-driver", tmp_path / "proc")
    except RuntimeError as error:
        assert str(error) == "no Tenstorrent devices detected in sysfs"
    else:
        raise AssertionError("missing devices must enter monitor recovery")


def test_passive_backend_does_not_open_device_nodes(tmp_path, monkeypatch):
    sys_class = tmp_path / "sys/class/tenstorrent"
    pci = tmp_path / "sys/devices/0000:02:00.0"
    device = sys_class / "tenstorrent!0"
    device.mkdir(parents=True)
    pci.mkdir(parents=True)
    (device / "device").symlink_to(pci, target_is_directory=True)
    write(device / "tt_card_type", "p150a\n")

    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert "/dev/tenstorrent" not in str(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    PassiveBackend(sys_class, tmp_path / "proc-driver", tmp_path / "proc")
