# Reuse the already measured native Feast/DuckDB/Redis runtime; add only committed separated-store job inputs.
FROM iot-coursework-feast:0.67.0
USER root
COPY feature_store/__init__.py feature_store/common.py feature_store/outbox.py feature_store/push.py feature_store/staged_push.py feature_store/staging_contract.py feature_store/staged_checks.py /opt/iot-release/feature_store/
COPY tests/fixtures/stream-finalized-native.json /opt/iot-release/tests/fixtures/
COPY docs/evidence/stream-push/warm/feast/source-report.json /opt/iot-release/docs/evidence/stream-push/warm/feast/
COPY pipelines/stream_job.py pipelines/stream_contract.py pipelines/stream_verification.py /opt/iot-release/pipelines/
ENV PYTHONPATH=/opt/iot-release
USER 10001:10001
ENTRYPOINT ["python"]
