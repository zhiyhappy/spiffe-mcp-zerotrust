# The official SPIRE images are distroless (no shell), but the demo needs a shell
# to run the one-time join-token bootstrap + entry registration. This tiny alpine
# image ships both spire-server and spire-agent binaries plus bash.
FROM alpine:3.20

ARG SPIRE_VERSION=1.9.6
ARG TARGETARCH=amd64

RUN apk add --no-cache bash curl ca-certificates \
 && curl -sSLf "https://github.com/spiffe/spire/releases/download/v${SPIRE_VERSION}/spire-${SPIRE_VERSION}-linux-${TARGETARCH}-musl.tar.gz" \
      -o /tmp/spire.tar.gz \
 && tar -xzf /tmp/spire.tar.gz -C /opt \
 && ln -s "/opt/spire-${SPIRE_VERSION}/bin/spire-server" /usr/bin/spire-server \
 && ln -s "/opt/spire-${SPIRE_VERSION}/bin/spire-agent"  /usr/bin/spire-agent \
 && rm -f /tmp/spire.tar.gz

# Default command is overridden per-service by the entrypoint scripts.
CMD ["spire-server", "run"]
