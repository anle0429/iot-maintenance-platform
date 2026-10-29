# Copy the source-tested MCP package over its verified retained runtime dependencies.
ARG RUNTIME=iot-coursework-mcp:mcp-2cf8e6898c93
FROM ${RUNTIME}
USER root
COPY services/__init__.py /workspace/services/__init__.py
COPY services/mcp_tools /workspace/services/mcp_tools
COPY services/predictor/__init__.py services/predictor/contracts.py /workspace/services/predictor/
COPY services/drift_api/__init__.py services/drift_api/contracts.py /workspace/services/drift_api/
COPY rag/__init__.py rag/store.py rag/embeddings.py rag/retrieval.py /workspace/rag/
COPY infra/rag/embedding.lock.json /workspace/infra/rag/embedding.lock.json
COPY knowledge/catalog.json /workspace/knowledge/catalog.json
USER 10001:10001
