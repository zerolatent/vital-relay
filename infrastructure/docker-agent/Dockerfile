FROM python:3.14.6-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS agent-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build
COPY infrastructure/docker-agent/requirements.lock ./requirements.lock
RUN python -m venv /opt/vital-relay \
    && /opt/vital-relay/bin/python -m pip install \
        --no-cache-dir --require-hashes --only-binary=:all: \
        --requirement requirements.lock


FROM python:3.14.6-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS agent

ENV PATH=/opt/vital-relay/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/opt/vital-relay/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 65532 vitalrelay \
    && useradd --uid 65532 --gid 65532 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin vitalrelay

COPY --from=agent-builder /opt/vital-relay /opt/vital-relay
COPY backend/src/vital_relay /opt/vital-relay/src/vital_relay
RUN chown -R root:root /opt/vital-relay \
    && chmod -R a-w /opt/vital-relay

WORKDIR /run/vital-relay
USER 65532:65532
ENTRYPOINT ["python", "-m", "vital_relay.agent.worker"]


FROM python:3.14.6-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS gateway-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 65532 gateway \
    && useradd --uid 65532 --gid 65532 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin gateway

WORKDIR /gateway
USER 65532:65532


FROM gateway-base AS vllm_gateway

COPY infrastructure/docker-agent/vllm_gateway.py ./vllm_gateway.py
ENTRYPOINT ["python", "/gateway/vllm_gateway.py"]


FROM gateway-base AS tool_proxy_gateway

COPY infrastructure/docker-agent/tool_proxy_gateway.py ./tool_proxy_gateway.py
ENTRYPOINT ["python", "/gateway/tool_proxy_gateway.py"]
