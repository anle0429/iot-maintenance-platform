# Add the tested CloudEvent acknowledgement to an inspected local drift image; preserve its locked dependencies.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER 0
COPY services/drift_api/app.py services/drift_api/events.py /app/services/drift_api/
COPY models/release/ /app/production/
USER 10001:10001
