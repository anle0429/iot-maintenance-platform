# Isolate the supported PyFlink runtime and matching Kafka connector from host Python.
FROM flink:1.20.3-scala_2.12-java17@sha256:6a2b771c073b523e43d72b46e338ce6cef25e969965444f6afe1b0bb15fa9772
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-dev build-essential \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && pip3 install --no-cache-dir apache-flink==1.20.3 \
    && apt-get purge -y --auto-remove build-essential python3-dev && rm -rf /var/lib/apt/lists/*
ADD --chmod=0644 --checksum=sha256:b860724fd97d7da0901bfc3859680442f4c1aa2b35ab944407558b393a1cf93e https://repo.maven.apache.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.4.0-1.20/flink-sql-connector-kafka-3.4.0-1.20.jar /opt/flink/lib/flink-sql-connector-kafka-3.4.0-1.20.jar
RUN install -d -o flink -g flink /var/lib/iot-ledgers
ENV PYTHONPATH=/workspace PYFLINK_CLIENT_EXECUTABLE=python3
WORKDIR /workspace
USER flink
ENTRYPOINT ["python3"]
