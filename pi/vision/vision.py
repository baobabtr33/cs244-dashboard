"""Run the Ultralytics Raspberry Pi camera pipeline unchanged and measure its FPS.

Starts the two commands from the Ultralytics Raspberry Pi guide:

    rpicam-vid -n -t 0 --inline --listen -o tcp://127.0.0.1:8888
    yolo predict model=<MODEL> source=tcp://127.0.0.1:8888

and counts the per-frame lines each one already prints (rpicam-vid needs -v 2
for that). Once a second it writes camera_fps, yolo_fps and yolo_inference_ms
to METRICS_DIR, where the gRPC server picks them up. Full output of both
commands is kept in logs/. Uses only the standard library.
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
METRICS_DIR = Path(os.environ.get("METRICS_DIR", "/tmp/pi-monitor"))
MODEL = os.environ.get("MODEL", "yolo26n.pt")
STREAM = os.environ.get("STREAM", "tcp://127.0.0.1:8888")
LOGS = HERE / "logs"

CAMERA_CMD = ["rpicam-vid", "-n", "-t", "0", "--inline", "--listen", "-v", "2", "-o", STREAM]
# save=False: the CLI saves annotated video by default, which would fill the SD card.
YOLO_CMD = ["uv", "run", "--project", str(HERE), "yolo", "predict",
            f"model={MODEL}", f"source={STREAM}", "save=False"]

CAMERA_FRAME = re.compile(r"^Viewfinder frame \d+")
YOLO_FRAME = re.compile(r"(\d+(?:\.\d+)?)ms\s*$")  # "... 480x640 1 person, 85.3ms"


class Meter:
    """Counts frames (and optional per-frame ms) between flushes."""

    def __init__(self, fps_name, ms_name=None):
        self.fps_name, self.ms_name = fps_name, ms_name
        self.lock = threading.Lock()
        self.count, self.ms_total = 0, 0.0

    def frame(self, ms=None):
        with self.lock:
            self.count += 1
            if ms is not None:
                self.ms_total += ms

    def flush(self, elapsed):
        with self.lock:
            count, ms_total = self.count, self.ms_total
            self.count, self.ms_total = 0, 0.0
        write_metric(self.fps_name, count / elapsed)
        if self.ms_name and count:
            write_metric(self.ms_name, ms_total / count)


def write_metric(name, value):
    tmp = METRICS_DIR / f".{name}.tmp"
    tmp.write_text(f"{value:.2f}\n")
    os.replace(tmp, METRICS_DIR / name)  # atomic: the server never reads a partial value


def pump(proc, log_path, on_line):
    """Copy a child's output to its log file and feed each line to on_line."""
    with open(log_path, "w") as log:
        for line in proc.stdout:
            log.write(line)
            log.flush()
            on_line(line.rstrip())


def start(cmd, log_name, on_line):
    proc = subprocess.Popen(
        cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    threading.Thread(target=pump, args=(proc, LOGS / log_name, on_line), daemon=True).start()
    return proc


def main():
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    camera = Meter("camera_fps")
    yolo = Meter("yolo_fps", "yolo_inference_ms")

    def on_camera(line):
        if CAMERA_FRAME.match(line):
            camera.frame()

    def on_yolo(line):
        m = YOLO_FRAME.search(line)
        if m:
            yolo.frame(float(m.group(1)))

    procs = [start(CAMERA_CMD, "rpicam.log", on_camera)]
    time.sleep(2)  # let rpicam-vid open the listening socket first
    procs.append(start(YOLO_CMD, "yolo.log", on_yolo))
    print(f"camera pid {procs[0].pid}, yolo pid {procs[1].pid}, metrics -> {METRICS_DIR}")

    def shutdown(*_):
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        for name in ("camera_fps", "yolo_fps", "yolo_inference_ms"):
            (METRICS_DIR / name).unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    last = time.monotonic()
    while True:
        time.sleep(1)
        now = time.monotonic()
        camera.flush(now - last)
        yolo.flush(now - last)
        last = now
        for p in procs:
            if p.poll() is not None:
                print(f"{p.args[0]} exited with {p.returncode}; see {LOGS}/")
                shutdown()


if __name__ == "__main__":
    main()
