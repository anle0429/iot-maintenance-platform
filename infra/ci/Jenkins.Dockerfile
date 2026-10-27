# Private local controller; the official LTS runtime and direct plugins are versioned.
FROM jenkins/jenkins:2.580.1-jdk21@sha256:a660310e39ade10631f774bacd5219de767dfd08947ea5c32a739b5e5bb382c1
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
USER jenkins
RUN jenkins-plugin-cli --plugins \
    workflow-aggregator:608.v67378e9d3db_1 \
    pipeline-model-definition:2.2293.v6e7193cec599 \
    credentials-binding:728.v902a_273b_8947 \
    credentials:1514.vb_01eb_014e7d4 \
    plain-credentials:199.v9f8e1f741799
COPY infra/ci/Jenkinsfile /opt/iot-ci/Jenkinsfile
COPY infra/ci/jenkins-bootstrap.groovy /usr/share/jenkins/ref/init.groovy.d/coursework.groovy
