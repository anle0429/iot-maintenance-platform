# Keep Spark's Python path unchanged; only the registration subprocess imports this SDK directory.
FROM iot-coursework-spark:3.5.9
USER root
COPY requirements-spark.lock /tmp/registry-sdk.lock
RUN pip3 install --no-cache-dir --disable-pip-version-check --target /opt/registry-sdk -r /tmp/registry-sdk.lock \
    && PYTHONPATH=/opt/registry-sdk python3 -m pip check
USER spark
