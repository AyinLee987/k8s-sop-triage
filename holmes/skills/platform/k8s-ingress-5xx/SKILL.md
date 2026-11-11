---
name: k8s-ingress-5xx
description: 通过 Ingress 访问服务返回 502 / 503 / 504 的通用排查。
---

# Ingress 5xx 排查

1. 先确认请求是否经过 Ingress；集群内部直接访问 Service 的流量不适用本文。
2. 502：后端 Pod 在请求中途断开，常见于滚动发布时没有优雅退出。
3. 503：Ingress 找不到可用后端，检查 Service 的 endpoints。
4. 504：后端处理超过了 Ingress 的超时时间。
