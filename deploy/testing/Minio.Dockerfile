# Disposable CI fixture built from pinned upstream source. Never replaces dev storage.
FROM golang:1.24.9-bookworm AS build
ENV CGO_ENABLED=0 GOMAXPROCS=2
RUN go install github.com/minio/minio@7aac2a2c5b7c882e68c1ce017d8256be2feea27f
RUN go install github.com/minio/mc@77f82e18b5401a65958f1619df6ebb994634bd88

FROM debian:bookworm-slim AS binaries
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /go/bin/minio /usr/local/bin/minio
COPY --from=build /go/bin/mc /usr/local/bin/mc

FROM binaries AS client
ENTRYPOINT ["mc"]

FROM binaries AS server
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
