#!/usr/bin/env bash
# Cloud VM only: build the api variants (lab_variants/make_variants.py) and import them into k3s.
# Usage: bash scripts/build_variants_cloud.sh /path/to/tiktok-k8s-lab
set -euo pipefail
LAB=${1:?usage: build_variants_cloud.sh /path/to/tiktok-k8s-lab}
REPO=$(cd "$(dirname "$0")/.." && pwd)
B=$(mktemp -d)
python3 "$REPO/lab_variants/make_variants.py" "$LAB" "$B/variants"
pip download -q --only-binary=:all: --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
  --python-version 3.12 --implementation cp -r "$LAB/requirements.txt" -d "$B/wheelhouse"
for v in v3 v3.1; do
  C="$B/ctx-$v"; mkdir -p "$C/scripts"
  cp -r "$B/variants/$v/app" "$C/app"; cp "$LAB/scripts/client.py" "$C/scripts/"; cp "$LAB/requirements.txt" "$C/"; cp -r "$B/wheelhouse" "$C/wheelhouse"
  chmod -R a+rX "$C"
  cat > "$C/Dockerfile" <<'DOCKER'
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
COPY wheelhouse /tmp/wheelhouse
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheelhouse -r requirements.txt && rm -rf /tmp/wheelhouse
COPY app ./app
COPY scripts/client.py ./scripts/client.py
RUN useradd --uid 10001 --create-home lab
ARG APP_VERSION=v1
ENV APP_VERSION=$APP_VERSION
USER 10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10", "--no-access-log"]
DOCKER
  docker build -q --build-arg APP_VERSION=$v -t tiktok-k8s-lab:$v "$C" >/dev/null
  docker save tiktok-k8s-lab:$v | k3s ctr images import - >/dev/null
  echo "imported tiktok-k8s-lab:$v"
done
rm -rf "$B"
