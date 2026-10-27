# Package the tested data-pipeline source over the already verified native runtime.
ARG RUNTIME=iot-coursework-spark:3.5.9
FROM ${RUNTIME}
COPY generator /opt/iot-release/generator
COPY pipelines /opt/iot-release/pipelines
ENV PYTHONPATH=/opt/iot-release
WORKDIR /work
