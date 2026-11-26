"""gRPC endpoint on the Raspberry Pi that serves system metrics on demand.

Each collector returns a list of Metric messages and runs on every GetMetrics
call. Add a metric by writing a collector and appending it to COLLECTORS. The
desktop bridge exports every metric it receives as rpi_<name>.
"""

import os
import re
import socket
import subprocess
import threading
import time
from concurrent import futures
from pathlib import Path

import grpc

import pimonitor_pb2
import pimonitor_pb2_grpc

PORT = int(os.environ.get("PORT", "50051"))


def metric(name, value, help_text, **labels):
    return pimonitor_pb2.Metric(name=name, value=float(value), help=help_text, labels=labels)


def vcgencmd(*args):
    return subprocess.run(
        ["vcgencmd", *args], capture_output=True, text=True, timeout=5, check=True
    ).stdout


def cpu_temp():
    """`vcgencmd measure_temp` prints e.g. temp=48.3'C.

    Falls back to the kernel thermal zone if vcgencmd is unavailable.
    """
    try:
        value = float(re.search(r"temp=([\d.]+)", vcgencmd("measure_temp")).group(1))
    except Exception:
        value = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0
    return [metric("cpu_temperature_celsius", value, "SoC temperature from vcgencmd measure_temp.")]


# get_throttled bit -> (flag, when). Bits 0-3 are current state, 16-19 are sticky since boot.
THROTTLE_BITS = {
    0: ("under_voltage", "now"),
    1: ("freq_capped", "now"),
    2: ("throttled", "now"),
    3: ("soft_temp_limit", "now"),
    16: ("under_voltage", "since_boot"),
    17: ("freq_capped", "since_boot"),
    18: ("throttled", "since_boot"),
    19: ("soft_temp_limit", "since_boot"),
}


def throttling():
    """`vcgencmd get_throttled` prints e.g. throttled=0x50000."""
    raw = int(re.search(r"throttled=(0x[0-9a-fA-F]+)", vcgencmd("get_throttled")).group(1), 16)
    out = [metric("throttled_raw", raw, "Raw bitmask from vcgencmd get_throttled.")]
    for bit, (flag, when) in THROTTLE_BITS.items():
        out.append(
            metric("throttle_flag", (raw >> bit) & 1, "Decoded get_throttled bits.", flag=flag, when=when)
        )
    return out


_cpu_lock = threading.Lock()
_cpu_prev = None


def _cpu_times():
    """Return (idle, total) jiffies from the aggregate line of /proc/stat."""
    fields = [int(v) for v in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:9]]
    return fields[3] + fields[4], sum(fields)  # idle + iowait; user..steal


def cpu_usage():
    """CPU busy percent since the previous call (first call samples over 250 ms)."""
    global _cpu_prev
    with _cpu_lock:
        prev = _cpu_prev
        if prev is None:
            prev = _cpu_times()
            time.sleep(0.25)
        cur = _cpu_times()
        _cpu_prev = cur
    d_idle, d_total = cur[0] - prev[0], cur[1] - prev[1]
    value = 100.0 * (1 - d_idle / d_total) if d_total > 0 else 0.0
    return [metric("cpu_usage_percent", value, "CPU busy percent across all cores.")]


def memory():
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, rest = line.split(":", 1)
        info[key] = int(rest.split()[0]) * 1024  # kB -> bytes
    total, available = info["MemTotal"], info["MemAvailable"]
    return [
        metric("memory_total_bytes", total, "Total RAM."),
        metric("memory_used_bytes", total - available, "RAM in use (total - available)."),
        metric("memory_used_percent", 100.0 * (total - available) / total, "RAM in use, percent."),
    ]


METRICS_DIR = Path(os.environ.get("METRICS_DIR", "/tmp/pi-monitor"))
METRICS_MAX_AGE = 5.0  # seconds; older files mean the writer stopped
FILE_METRICS = {
    "camera_fps": "Frames per second delivered by rpicam-vid.",
    "yolo_fps": "Frames per second processed by yolo predict.",
    "yolo_inference_ms": "Mean YOLO inference time per frame, ms.",
}


def file_metrics():
    """Single-number files written by vision/vision.py. Missing or stale files are skipped."""
    out = []
    for name, help_text in FILE_METRICS.items():
        path = METRICS_DIR / name
        try:
            if time.time() - path.stat().st_mtime > METRICS_MAX_AGE:
                continue
            out.append(metric(name, float(path.read_text()), help_text))
        except (FileNotFoundError, ValueError):
            continue
    return out


COLLECTORS = [cpu_temp, throttling, cpu_usage, memory, file_metrics]


class PiMonitor(pimonitor_pb2_grpc.PiMonitorServicer):
    def GetMetrics(self, request, context):
        metrics = []
        for collect in COLLECTORS:
            try:
                metrics.extend(collect())
            except Exception as e:
                print(f"[{collect.__name__}] collect failed: {e}")
        return pimonitor_pb2.GetMetricsResponse(
            hostname=socket.gethostname(),
            timestamp_ms=int(time.time() * 1000),
            metrics=metrics,
        )


if __name__ == "__main__":
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pimonitor_pb2_grpc.add_PiMonitorServicer_to_server(PiMonitor(), server)
    server.add_insecure_port(f"0.0.0.0:{PORT}")
    server.start()
    print(f"PiMonitor gRPC server listening on :{PORT}")
    server.wait_for_termination()
