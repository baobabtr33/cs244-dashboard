"""Pulls metrics from Raspberry Pi gRPC endpoints and exposes them to Prometheus.

Every Prometheus scrape of /metrics triggers one GetMetrics call per Pi in
PI_TARGETS. Each received metric is exported as rpi_<name>{pi, target}, and
rpi_up{target} reports whether the call succeeded.

It also serves the newest image in IMAGES_DIR for the dashboard image panel:
/images/ (viewer page), /images/latest.json, /images/file/<name>.
"""

import json
import re
import struct
import zlib
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from urllib.parse import unquote

import grpc

import pimonitor_pb2
import pimonitor_pb2_grpc

PORT = int(os.environ.get("PORT", "9110"))
TIMEOUT = float(os.environ.get("GRPC_TIMEOUT", "3"))
TARGETS = [t.strip() for t in os.environ["PI_TARGETS"].split(",") if t.strip()]

IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "/images"))
IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".bmp": "image/bmp",
    ".pgm": "image/png", ".ppm": "image/png", ".pnm": "image/png",  # converted to PNG on the fly
}
NETPBM = {".pgm", ".ppm", ".pnm"}
VIEWER = (Path(__file__).resolve().parent / "viewer.html").read_bytes()

STUBS = {t: pimonitor_pb2_grpc.PiMonitorStub(grpc.insecure_channel(t)) for t in TARGETS}


def escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render():
    families = {}  # metric name -> (help, [sample lines])
    up = []
    for target, stub in STUBS.items():
        try:
            resp = stub.GetMetrics(pimonitor_pb2.GetMetricsRequest(), timeout=TIMEOUT)
        except grpc.RpcError as e:
            print(f"[{target}] GetMetrics failed: {e.code().name}")
            up.append(f'rpi_up{{target="{target}"}} 0')
            continue
        up.append(f'rpi_up{{target="{target}"}} 1')
        for m in resp.metrics:
            name = f"rpi_{m.name}"
            labels = {"pi": resp.hostname, "target": target, **m.labels}
            label_str = ",".join(f'{k}="{escape(v)}"' for k, v in labels.items())
            families.setdefault(name, (m.help, []))[1].append(f"{name}{{{label_str}}} {m.value}")

    lines = []
    for name, (help_text, samples) in families.items():
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", *samples]
    lines += ["# HELP rpi_up 1 if the Pi gRPC endpoint answered.", "# TYPE rpi_up gauge", *up]
    return ("\n".join(lines) + "\n").encode()


def latest_image():
    """Newest image file in IMAGES_DIR by mtime, or None."""
    try:
        files = [f for f in IMAGES_DIR.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_TYPES]
    except FileNotFoundError:
        return None
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


def image_info():
    f = latest_image()
    info = {"name": f.name, "mtime_ms": int(f.stat().st_mtime * 1000)} if f else {"name": None}
    return json.dumps(info).encode()


def _png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def netpbm_to_png(data):
    """Convert binary PGM (P5) or PPM (P6) bytes to PNG. Browsers cannot show netpbm."""
    fields, pos = [], 0
    while len(fields) < 4:  # magic, width, height, maxval; '#' starts a comment
        m = re.compile(rb"\s*(?:#[^\n]*\n\s*)*(\S+)").match(data, pos)
        if not m:
            raise ValueError("truncated netpbm header")
        fields.append(m.group(1))
        pos = m.end()
    magic = fields[0]
    if magic not in (b"P5", b"P6"):
        raise ValueError(f"unsupported netpbm type {magic!r}; only binary P5/P6")
    width, height, maxval = map(int, fields[1:])
    pos += 1  # single whitespace byte before pixel data
    channels = 1 if magic == b"P5" else 3
    depth = 16 if maxval > 255 else 8
    row = width * channels * depth // 8
    pixels = data[pos:pos + row * height]
    if maxval not in (255, 65535):  # stretch odd ranges to the full PNG range
        full = (1 << depth) - 1
        if depth == 8:
            pixels = bytes(min(full, v * full // maxval) for v in pixels)
        else:
            vals = struct.unpack(f">{len(pixels) // 2}H", pixels)
            pixels = struct.pack(f">{len(vals)}H", *(min(full, v * full // maxval) for v in vals))
    raw = b"".join(b"\0" + pixels[y * row:(y + 1) * row] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, depth, 0 if channels == 1 else 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(raw, 1)) + _png_chunk(b"IEND", b""))


_png_cache = {}  # (path, mtime) -> png bytes, so each netpbm file is converted once


def image_file(name):
    """Bytes of IMAGES_DIR/<name>; rejects anything outside the folder."""
    path = IMAGES_DIR / name
    suffix = path.suffix.lower()
    if "/" in name or suffix not in IMAGE_TYPES or not path.is_file():
        return None
    if suffix not in NETPBM:
        return path.read_bytes(), IMAGE_TYPES[suffix]
    key = (name, path.stat().st_mtime)
    if key not in _png_cache:
        _png_cache.clear()
        try:
            _png_cache[key] = netpbm_to_png(path.read_bytes())
        except ValueError as e:
            print(f"[images] cannot convert {name}: {e}")
            return None
    return _png_cache[key], "image/png"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        status = 200
        if path == "/metrics":
            body, ctype = render(), "text/plain; version=0.0.4"
        elif path in ("/images", "/images/"):
            body, ctype = VIEWER, "text/html; charset=utf-8"
        elif path == "/images/latest.json":
            body, ctype = image_info(), "application/json"
        elif path.startswith("/images/file/") and (found := image_file(path[len("/images/file/"):])):
            body, ctype = found
        else:
            body, ctype, status = b"see /metrics or /images/\n", "text/plain", 404
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"bridge on :{PORT}/metrics, pulling from {', '.join(TARGETS)}; images from {IMAGES_DIR}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
