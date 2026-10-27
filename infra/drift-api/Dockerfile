# Use the same pinned lightweight Python/API dependencies as the verified predictor.
FROM python:3.12-slim-bookworm@sha256:fc169880a4d941f3142c0e2e623f05f246150add19dc36fc9224df20fb19e5fd
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app HOME=/tmp
COPY infra/predictor/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock && pip check
WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/predictor/__init__.py services/predictor/contracts.py /app/services/predictor/
COPY services/drift_api /app/services/drift_api
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn","services.drift_api.app:create_app","--factory","--host","0.0.0.0","--port","8080","--workers","1","--no-access-log"]
