FROM iot-coursework-feast:0.67.0
USER root
COPY constraints.txt /tmp/versioning-constraints.txt
RUN pip install --no-cache-dir -c /tmp/versioning-constraints.txt mlflow-skinny==3.17.2 && pip check
USER 10001:10001
