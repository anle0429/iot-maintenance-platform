# Package the registry-verified version1 coefficients inside a small, non-root HTTP model server.
FROM python:3.12-slim-bookworm@sha256:fc169880a4d941f3142c0e2e623f05f246150add19dc36fc9224df20fb19e5fd
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app HOME=/tmp IOT_MODEL_BUNDLE=/app/models/pump-failure-v1
COPY infra/predictor/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock && pip check
WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/predictor /app/services/predictor
COPY ml/__init__.py ml/protocol.py ml/training_contract.py /app/ml/
COPY models/pump-failure-v1 /app/models/pump-failure-v1
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn","services.predictor.app:create_app","--factory","--host","0.0.0.0","--port","8080","--workers","1","--no-access-log"]
