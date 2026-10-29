# Extend the inspected native MCP dependency image; observer code never imports or executes its MCP business factory.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
COPY infra/observability/requirements.lock /tmp/observability.lock
COPY infra/llm-observability/requirements-http.lock /tmp/observer-http.lock
RUN pip install --no-cache-dir --disable-pip-version-check --no-deps -r /tmp/observability.lock -r /tmp/observer-http.lock && pip check
COPY llm_observability /workspace/llm_observability
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["python","-m","uvicorn","llm_observability.app:create_app","--factory","--host","0.0.0.0","--port","8081","--workers","1","--no-access-log"]
