# Package exactly the tested Feast/materialization source over its existing SDK runtime.
ARG RUNTIME=iot-coursework-feast:0.67.0
FROM ${RUNTIME}
COPY feature_store /opt/iot-release/feature_store
COPY ml/__init__.py ml/protocol.py /opt/iot-release/ml/
COPY ml/protocols/medium-60d-v1.json /opt/iot-release/ml/protocols/
ENV PYTHONPATH=/opt/iot-release
WORKDIR /workspace
