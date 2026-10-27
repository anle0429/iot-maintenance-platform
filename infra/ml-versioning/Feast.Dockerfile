# Preserve Feast/Arrow26 and add only the pinned MLflow tracking SDK.
FROM iot-coursework-feast:0.67.0
USER root
COPY requirements-feast.lock /tmp/versioned-feast.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/versioned-feast.lock && pip check
USER 10001:10001
