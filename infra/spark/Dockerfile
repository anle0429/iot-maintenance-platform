# Run the coursework batch job with a fixed Spark/JDK runtime and PostgreSQL driver.
FROM apache/spark:3.5.9-scala2.12-java17-python3-ubuntu@sha256:f3d6eaa8bab8ec2e38f3c3918a5b2f8b253c95bcb19f9e87ad0e0cdacf9df2d5
USER root
ADD --chmod=0644 --checksum=sha256:73914527305a40cce504b0d3d90b23caf565136912d607ae8ae7c5895512332c https://jdbc.postgresql.org/download/postgresql-42.7.14.jar /opt/spark/jars/postgresql-42.7.14.jar
RUN pip3 install --no-cache-dir --disable-pip-version-check numpy==1.26.4
ENV PYTHONPATH=/workspace PYSPARK_PYTHON=python3
WORKDIR /workspace
USER spark
ENTRYPOINT ["/opt/spark/bin/spark-submit"]
