# The official checksum-verified Jaeger binary is supplied only in the native build context, never stored in Git.
FROM python:3.12-slim-bookworm@sha256:fc169880a4d941f3142c0e2e623f05f246150add19dc36fc9224df20fb19e5fd
COPY --chmod=0555 jaeger /usr/local/bin/jaeger
ENV GOMEMLIMIT=384MiB GOMAXPROCS=2 HOME=/tmp
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/jaeger"]
CMD ["--config=/config/config.yaml"]
