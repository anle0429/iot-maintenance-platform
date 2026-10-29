# The deployer verifies the retained CPU RAG dependency image before this bounded extension build.
ARG BASE_IMAGE=iot-coursework-rag:deps-c3b696c3955c
FROM ${BASE_IMAGE} AS runtime
USER root
COPY infra/mcp/requirements.txt /tmp/mcp-requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/mcp-requirements.txt && pip check
COPY services/__init__.py /workspace/services/__init__.py
COPY services/mcp_tools /workspace/services/mcp_tools
COPY services/predictor/__init__.py services/predictor/contracts.py /workspace/services/predictor/
COPY services/drift_api/__init__.py services/drift_api/contracts.py /workspace/services/drift_api/
COPY rag/__init__.py rag/store.py rag/embeddings.py rag/retrieval.py /workspace/rag/
COPY infra/rag/embedding.lock.json /workspace/infra/rag/embedding.lock.json
COPY knowledge/catalog.json /workspace/knowledge/catalog.json
ENV HF_HUB_OFFLINE=1
USER 10001:10001
ENTRYPOINT ["python","-m","uvicorn","services.mcp_tools.app:create_app","--factory","--host","0.0.0.0","--port","8080","--no-access-log"]

FROM runtime AS test
USER root
COPY infra/mcp/requirements-tests.txt /tmp/mcp-tests.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/mcp-tests.txt && pip check
COPY tests/mcp /tests/mcp
COPY services/drift_api/detector.py services/drift_api/runtime.py /workspace/services/drift_api/
ENV COVERAGE_FILE=/proof/.coverage HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis
USER 10001:10001
ENTRYPOINT ["python"]
