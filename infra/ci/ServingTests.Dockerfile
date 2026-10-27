# Run the current serving API gates in a fresh Linux source copy with observed pinned test dependencies.
FROM python:3.12-slim-bookworm@sha256:fc169880a4d941f3142c0e2e623f05f246150add19dc36fc9224df20fb19e5fd
COPY infra/ci/requirements-serving-tests.lock /tmp/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt && pip check
RUN mkdir /work && chown 10001:10001 /work
WORKDIR /work
COPY --chown=10001:10001 . /work/
ENV PYTHONPATH=/work HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["python", "/work/scripts/test-serving-ci.py"]
