# Constructed comparison baseline: same application/lock/model as the serving Dockerfile, full Python base.
# BASE_IMAGE must be resolved to an official Python image digest by the benchmark runner.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG MODEL_BUNDLE=models/pump-failure-v1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app HOME=/tmp IOT_MODEL_BUNDLE=/app/model
COPY infra/predictor/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock && pip check
WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/predictor /app/services/predictor
COPY ml/__init__.py ml/protocol.py ml/training_contract.py /app/ml/
COPY ${MODEL_BUNDLE}/portable.json ${MODEL_BUNDLE}/lineage.json /app/model/
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn","services.predictor.app:create_app","--factory","--host","0.0.0.0","--port","8080","--workers","1","--no-access-log"]
