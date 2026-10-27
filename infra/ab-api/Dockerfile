# Reuse the verified native Feast SDK; mount both model pins/config and reference at deployment, never bake secrets or runtime data.
FROM iot-coursework-feast-versioned:0.67.0-3.17.2
ENV PYTHONPATH=/app HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FEAST_USAGE=False
WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/ab_api /app/services/ab_api
COPY services/feature_api/__init__.py services/feature_api/runtime.py /app/services/feature_api/
COPY services/predictor/__init__.py services/predictor/contracts.py services/predictor/binding.py /app/services/predictor/
COPY services/drift_api/__init__.py services/drift_api/contracts.py services/drift_api/detector.py /app/services/drift_api/
COPY ml/__init__.py ml/protocol.py /app/ml/
USER 10001:10001
ENTRYPOINT ["uvicorn"]
CMD ["services.ab_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
