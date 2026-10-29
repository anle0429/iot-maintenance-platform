# Package the tested RAG application on the retained dependency image; model cache, state and credentials stay outside.
ARG RUNTIME=iot-coursework-rag:deps-c3b696c3955c
FROM ${RUNTIME}
COPY rag /opt/iot-rag-release/rag
COPY knowledge /opt/iot-rag-release/knowledge
COPY infra/rag/embedding.lock.json /opt/iot-rag-release/infra/rag/embedding.lock.json
ENV PYTHONPATH=/opt/iot-rag-release
USER 10001:10001
ENTRYPOINT ["python"]
