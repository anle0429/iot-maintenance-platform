# Overlay instrumentation on an operator-selected, inspected serving image; retain its business code and model bytes.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER 0
COPY infra/observability/requirements.lock /tmp/observability.lock
RUN pip install --no-cache-dir --disable-pip-version-check --no-deps -r /tmp/observability.lock && pip check
COPY services/observed.py /app/services/observed.py
USER 10001:10001
ENTRYPOINT ["uvicorn"]
CMD ["services.observed:create_app","--factory","--host","0.0.0.0","--port","8080","--workers","1","--no-access-log"]
