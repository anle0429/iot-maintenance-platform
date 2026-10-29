# Package the already authenticated official static exporter; no host tooling or application dependencies are added.
FROM scratch
COPY --chmod=0555 node_exporter /node_exporter
COPY LICENSE NOTICE /
USER 65534:65534
ENTRYPOINT ["/node_exporter"]
