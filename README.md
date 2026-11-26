# cs244-dashboard

```
Directory Tree with summary

├── README.md
├── desktop (Dashboard Setup)
│   ├── bridge 
│   ├── docker-compose.yml
│   ├── grafana (Frontend Service for visualization)
│   ├── images (storage for images received)
│   ├── prometheus (tsdb, data scraping)
│   │   └── prometheus.yml
│   └── run.sh
├── pi (Raspberry Pi Setup)
│   ├── pyproject.toml (uv - depedency management)
│   ├── server.py
│   ├── start.sh
├── proto
│   └── pimonitor.proto

```

## Data flow

```mermaid
flowchart LR
    subgraph PI["Raspberry Pi (pi/start.sh)"]
        direction TB
        OS["vcgencmd measure_temp / get_throttled<br/>/proc/stat, /proc/meminfo<br/>/sys/class/thermal (fallback)"]
        SRV["server.py :50051<br/>PiMonitor.GetMetrics()<br/>runs COLLECTORS per call"]
        OS -->|"read on every call"| SRV
    end

    subgraph DESKTOP["Desktop (desktop/run.sh, docker compose)"]
        direction TB
        BR["grpc-bridge :9110<br/>bridge.py"]
        IMG[("desktop/images/<br/>mounted at /images (read-only)")]
        PROM["Prometheus :9090<br/>scrape every 5s, 30d retention"]
        GRAF["Grafana :3000<br/>raspberry-pi.json"]
        USER(["Browser"])

        PROM -->|"GET /metrics"| BR
        BR -->|"rpi_&lt;name&gt;{pi,target,...}<br/>rpi_up{target}"| PROM
        GRAF -->|"PromQL"| PROM
        IMG -->|"newest file by mtime<br/>PGM/PPM converted to PNG"| BR
        USER -->|"dashboards"| GRAF
        USER -->|"image panel iframe<br/>/images/, /images/latest.json,<br/>/images/file/&lt;name&gt;"| BR
    end

    BR ==>|"gRPC GetMetricsRequest<br/>PI_TARGETS, timeout 3s"| SRV
    SRV ==>|"GetMetricsResponse<br/>hostname, timestamp_ms, metrics[]"| BR
```

### One scrape cycle

1. Prometheus requests `GET /metrics` from the bridge every 5 seconds.
2. The bridge calls `GetMetrics` on each Pi in `PI_TARGETS` (comma-separated `host:port`).
3. On the Pi, `server.py` runs every collector (`cpu_temp`, `throttling`, `cpu_usage`, `memory`) and returns a `GetMetricsResponse` (contract in `proto/pimonitor.proto`).
4. The bridge renders each metric as `rpi_<name>{pi, target, ...labels}` in Prometheus text format, plus `rpi_up{target}` (1 if the call succeeded, 0 if not).
5. Grafana queries Prometheus with PromQL to draw the panels.
