# The controller SDK is isolated from the preserved Feast runtime dependencies.
ARG RUNTIME=iot-coursework-feast-versioned:0.67.0-3.17.2
FROM ${RUNTIME}
USER root
COPY infra/ci/requirements-training-controller.lock /tmp/requirements.txt
RUN python -m venv /opt/kfp-venv \
    && /opt/kfp-venv/bin/python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && /opt/kfp-venv/bin/python -m pip check
ENV PATH=/opt/kfp-venv/bin:${PATH}
ENTRYPOINT ["python"]
