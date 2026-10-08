# torpedo

`torpedo` samples Tenstorrent telemetry from the kernel's passive sysfs and
hwmon interfaces and serves the most recent sample as JSON. It never imports
tt-smi, pyluwen, or tt-umd and never opens `/dev/tenstorrent`, so it does not
appear as a device owner in `tt-smi` or
`/proc/driver/tenstorrent/<n>/pids`. HTTP requests never touch hardware and
never wait for a telemetry read.

If a device reset temporarily removes its sysfs nodes, torpedo repeats passive
discovery with exponential backoff (up to 30 seconds). While it is recovering,
health checks return 503 and the snapshot contains no stale device values.
Normal sampling resumes automatically once the kernel recreates the nodes.

## Run

```sh
uv sync
uv run torpedo --host 0.0.0.0 --port 8080 --interval 1
curl http://127.0.0.1:8080/v1/devices
curl http://127.0.0.1:8080/healthz
```

The root path and `/v1/devices` return the same snapshot. `held` means at least
one process other than torpedo is listed by
`/proc/driver/tenstorrent/<n>/pids`; the associated process records are embedded
in each device and also returned at top level. Torpedo owns no device handles.

Compute occupancy is not exposed by the driver and remains JSON `null`.
Allocated GDDR is read passively from tt-metal's versioned, per-device
`/dev/shm/tt_device_*_memory` allocator region. Torpedo maps it read-only and
does not create a Metal context or allocate device memory for this measurement.
