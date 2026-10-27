# Isolate Feast's NumPy2/DuckDB/Redis SDK from Spark and Airflow dependencies.
FROM python:3.12-slim-bookworm@sha256:fc169880a4d941f3142c0e2e623f05f246150add19dc36fc9224df20fb19e5fd
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FEAST_USAGE=False HOME=/tmp OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /workspace
COPY requirements.lock /tmp/feast-requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/feast-requirements.txt && pip check
USER 10001:10001
ENTRYPOINT ["python"]
