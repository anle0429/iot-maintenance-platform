# Bind the tested application source to its native SDK image; deployment verifies the mounted copy too.
ARG RUNTIME=iot-coursework-feast-versioned:0.67.0-3.17.2
FROM ${RUNTIME}
COPY . /opt/iot-release
