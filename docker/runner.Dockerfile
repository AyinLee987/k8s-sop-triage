# Experiment runner for the Windows laptop: HolmesGPT + kubectl + this repo's harness, in Linux.
# It joins Docker's "kind" network and talks to the kind cluster's API server by its internal name.
# Build (from the repo root):  docker build -t k8s-sop-runner -f docker/runner.Dockerfile .
FROM python:3.13-slim

ARG KUBECTL_VERSION=v1.36.1
# HolmesGPT is pinned to the commit the cloud runs used (version 0.46.0).
ARG HOLMES_REF=27c623e

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates jq \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && kubectl version --client

RUN pip install --no-cache-dir "PyYAML>=6.0" "pytest>=8.0" \
 && pip install --no-cache-dir "holmesgpt @ git+https://github.com/HolmesGPT/holmesgpt.git@${HOLMES_REF}" \
 && holmes version

WORKDIR /work
ENTRYPOINT ["bash", "/work/scripts/laptop_entry.sh"]
