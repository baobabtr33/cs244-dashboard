# Build from the repo root: docker build -f desktop/bridge/Dockerfile -t grpc-bridge .
FROM debian:bookworm-slim

# Same Debian gRPC packages as the Pi image, so both sides match versions.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-grpcio python3-grpc-tools python3-pkg-resources python3-protobuf \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY proto/pimonitor.proto desktop/bridge/bridge.py desktop/bridge/viewer.html ./
RUN python3 -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. pimonitor.proto

EXPOSE 9110
CMD ["python3", "-u", "bridge.py"]
