# CPU embeddings and native Feast/Milvus are isolated from the retained ML stack.
FROM python:3.12-slim-bookworm@sha256:fc169880a4d941f3142c0e2e623f05f246150add19dc36fc9224df20fb19e5fd
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FEAST_USAGE=False HF_HOME=/models \
    HOME=/tmp OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
WORKDIR /workspace
RUN pip install --no-cache-dir --disable-pip-version-check torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
COPY infra/rag/requirements.txt /tmp/rag-requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/rag-requirements.txt && pip check
USER 10001:10001
ENTRYPOINT ["python"]
