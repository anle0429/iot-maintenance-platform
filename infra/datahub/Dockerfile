# Isolate the DataHub SDK's dependencies from the Airflow execution environment.
FROM python:3.12.12-slim-bookworm
RUN pip install --no-cache-dir acryl-datahub==1.6.0
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "pipelines.metadata_publish"]
