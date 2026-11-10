#!/usr/bin/env bash
# Ubuntu 版的 tiktok-k8s-lab 部署（对应原项目的 Setup.ps1 + Build.ps1 + Deploy.ps1）。
# 用法：bash scripts/setup_lab_linux.sh /path/to/tiktok-k8s-lab
# 前提：已安装 docker（当前用户在 docker 组）、kind、kubectl。
# 不会删除已有集群；同名集群已存在时直接复用。
set -euo pipefail

LAB_DIR="${1:?usage: setup_lab_linux.sh /path/to/tiktok-k8s-lab}"
NAME=tiktok-k8s-lab
CTX="kind-${NAME}"
NODE_IMAGE='kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed'
K="kubectl --context ${CTX}"

cd "$LAB_DIR"
mkdir -p .local

# 1. 集群
if kind get clusters | grep -qx "$NAME"; then
  echo "kind cluster $NAME exists; reusing"
else
  kind create cluster --name "$NAME" --config k8s/kind.yaml --image "$NODE_IMAGE" --wait 120s
fi
$K get nodes -o wide

# 2. 镜像
docker build --target runtime --build-arg APP_VERSION=v1 -t tiktok-k8s-lab:v1 .
docker pull redis:7.4.2-alpine
kind load docker-image tiktok-k8s-lab:v1 redis:7.4.2-alpine --name "$NAME"

# 3. Redis 密码（只写入 .local，不打印）
umask 077
if [[ ! -s .local/redis-password.txt ]]; then
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > .local/redis-password.txt
fi
printf 'requirepass %s\n' "$(cat .local/redis-password.txt)" > .local/redis-auth.conf

# 4. 部署
$K apply -f k8s/namespace.yaml
$K -n "$NAME" create secret generic redis-auth \
  --from-file=password=.local/redis-password.txt \
  --from-file=redis-auth.conf=.local/redis-auth.conf \
  --dry-run=client -o json | $K -n "$NAME" apply -f -
$K apply -k k8s
$K -n "$NAME" rollout status statefulset/redis --timeout=180s
$K -n "$NAME" rollout status deployment/api --timeout=180s
$K -n "$NAME" rollout status deployment/worker --timeout=180s
$K -n "$NAME" get pods,deploy,sts,svc,pvc -o wide
echo "Lab ready. Admin context: ${CTX}"
