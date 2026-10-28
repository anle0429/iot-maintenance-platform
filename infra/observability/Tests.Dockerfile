# Reuse the pinned native test dependencies; the sealed source is mounted separately and copied into fresh scratch.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER 0
COPY infra/observability/requirements.lock /tmp/observability.lock
RUN pip install --no-cache-dir --disable-pip-version-check --no-deps -r /tmp/observability.lock && pip check
USER 10001:10001
ENTRYPOINT ["python"]
