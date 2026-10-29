# Reuse inspected native test tooling; sealed observer sources are copied into fresh scratch by the quality runner.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
COPY infra/observability/requirements.lock /tmp/observability.lock
COPY infra/llm-observability/requirements-http.lock /tmp/observer-http.lock
COPY infra/llm-observability/requirements-tests.lock /tmp/observer-tests.lock
RUN pip install --no-cache-dir --disable-pip-version-check --no-deps -r /tmp/observability.lock -r /tmp/observer-http.lock -r /tmp/observer-tests.lock && pip check
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis
USER 10001:10001
ENTRYPOINT ["python"]
