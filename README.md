# torpedo

`torpedo` keeps one Tenstorrent UMD discovery context alive, samples `tt-smi`
telemetry in a background thread, and serves the most recent sample as JSON.
HTTP requests never initialize hardware and never wait for a telemetry read.

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
in each device and also returned at top level. Torpedo's own persistent handles
are excluded so the monitor does not make every device appear held.

The default `luwen` backend is fast and works on standalone PCIe boards. Use
`--backend umd` when full multi-chip Ethernet topology discovery is required.

Compute occupancy is not exposed by `tt-smi` 6.3.0 and remains JSON `null`.
Allocated GDDR is read passively from tt-metal's versioned, per-device
`/dev/shm/tt_device_*_memory` allocator region. Torpedo maps it read-only and
does not create a Metal context or allocate device memory for this measurement.
