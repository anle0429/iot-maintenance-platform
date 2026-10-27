# Isolate MLflow's server/model SDK from Feast's Arrow26 and Spark's NumPy1 environment.
FROM python:3.12-slim-bookworm@sha256:fc169880a4d941f3142c0e2e623f05f246150add19dc36fc9224df20fb19e5fd
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=/workspace
COPY requirements-mlflow.lock /tmp/requirements-mlflow.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements-mlflow.lock && pip check
WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["python"]
