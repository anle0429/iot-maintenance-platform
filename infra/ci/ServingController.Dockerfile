# Reuse the native Feast/MLflow SDK; no dependency upgrade and no model, source or kubeconfig baked into this controller.
FROM iot-coursework-feast-versioned:0.67.0-3.17.2
USER root
ENV FEAST_USAGE=False PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python"]
