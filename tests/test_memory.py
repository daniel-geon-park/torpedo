import struct

from torpedo.memory import REGION_MIN_SIZE, physical_dram_bytes, read_memory_stats


def test_reads_versioned_allocator_region(tmp_path):
    path = tmp_path / "tt_device_123_memory"
    region = bytearray(REGION_MIN_SIZE)
    struct.pack_into("=I", region, 0, 3)
    struct.pack_into("=Q", region, 8, 999)
    struct.pack_into("=Q", region, 32, 123)
    struct.pack_into("=I", region, 40, 1)
    struct.pack_into("=Q", region, 48, 4096)
    struct.pack_into("=i", region, 856, 42)
    struct.pack_into("=Q", region, 864, 2048)
    path.write_bytes(region)

    stats = read_memory_stats([str(path)])[1]
    assert stats.asic_id == 123
    assert stats.dram_used_bytes == 4096
    assert stats.process_dram_bytes == {42: 2048}


def test_rejects_unknown_layout(tmp_path):
    path = tmp_path / "tt_device_123_memory"
    region = bytearray(REGION_MIN_SIZE)
    struct.pack_into("=I", region, 0, 99)
    path.write_bytes(region)
    assert read_memory_stats([str(path)]) == {}


def test_p150_capacity_without_opening_a_device():
    info = {"board_info": {"board_type": "p150a"}}
    assert physical_dram_bytes(info) == 32 * 1024**3
