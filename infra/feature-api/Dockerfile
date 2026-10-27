# Reuse the verified native Feast SDK image; add only the read API and pinned model input contract.
FROM iot-coursework-feast:0.67.0
ENV PYTHONPATH=/app HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FEAST_USAGE=False
WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/feature_api /app/services/feature_api
COPY services/predictor/__init__.py services/predictor/contracts.py /app/services/predictor/
USER 10001:10001
ENTRYPOINT ["uvicorn"]
CMD ["services.feature_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
