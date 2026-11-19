FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

USER node
