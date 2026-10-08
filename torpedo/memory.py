from __future__ import annotations

import glob
import mmap
import os
import struct
from dataclasses import dataclass
from typing import Iterable

# tt-metal DeviceMemoryRegion v3. Keep this reader deliberately version-bound:
# silently interpreting a changed shared-memory layout would be worse than N/A.
REGION_VERSION = 3
REGION_MIN_SIZE = 8576
PROCESS_COUNT = 64
PROCESS_OFFSET = 856
PROCESS_SIZE = 120


@dataclass(frozen=True)
class MemoryStats:
    device_id: int
    asic_id: int
    dram_used_bytes: int
    updated_at_ns: int
    process_dram_bytes: dict[int, int]


def read_memory_stats(paths: Iterable[str] | None = None) -> dict[int, MemoryStats]:
    """Read tt-metal allocator statistics without opening a device context."""
    if paths is None:
        paths = glob.glob("/dev/shm/tt_device_*_memory")

    result: dict[int, MemoryStats] = {}
    for path in paths:
        try:
            with open(path, "rb") as file, mmap.mmap(
                file.fileno(), 0, access=mmap.ACCESS_READ
            ) as region:
                if len(region) < REGION_MIN_SIZE:
                    continue
                version = struct.unpack_from("=I", region, 0)[0]
                if version != REGION_VERSION:
                    continue
                updated_at_ns = struct.unpack_from("=Q", region, 8)[0]
                asic_id = struct.unpack_from("=Q", region, 32)[0]
                device_id = struct.unpack_from("=I", region, 40)[0]
                dram_used_bytes = struct.unpack_from("=Q", region, 48)[0]
                per_process: dict[int, int] = {}
                for slot in range(PROCESS_COUNT):
                    offset = PROCESS_OFFSET + slot * PROCESS_SIZE
                    pid = struct.unpack_from("=i", region, offset)[0]
                    if pid > 0:
                        per_process[pid] = struct.unpack_from("=Q", region, offset + 8)[0]
                result[device_id] = MemoryStats(
                    device_id=device_id,
                    asic_id=asic_id,
                    dram_used_bytes=dram_used_bytes,
                    updated_at_ns=updated_at_ns,
                    process_dram_bytes=per_process,
                )
        except (OSError, ValueError, struct.error):
            # Regions can disappear or be replaced while scanning.
            continue
    return result


def physical_dram_bytes(device_info: dict) -> int | None:
    """Return physical Blackhole GDDR capacity from enabled channel count."""
    gddr = device_info.get("gddr_telemetry", {})
    channels = gddr.get("channels", [])
    # Blackhole has eight 4-GiB GDDR channels. Limit this inference to the
    # eight-channel schema; other architectures need their own capacity source.
    if len(channels) == 8:
        enabled = sum(bool(channel.get("enabled")) for channel in channels)
        return enabled * 4 * 1024**3
    # The passive backend deliberately never opens a device to query channel
    # state. P150 cards have eight fixed 4-GiB Blackhole GDDR channels.
    board_type = str(device_info.get("board_info", {}).get("board_type", "")).lower()
    if board_type in {"p150", "p150a", "p150b"}:
        return 32 * 1024**3
    return None
