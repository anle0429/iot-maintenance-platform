# Build the static Linux binary with the recorded Go toolchain before the native Docker build.
FROM scratch
COPY iot-model-control-linux-amd64 /iot-model-control
USER 10001:10001
ENTRYPOINT ["/iot-model-control"]
