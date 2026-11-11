---
name: k8s-dns-resolution
description: Kubernetes 集群内 DNS 解析失败或解析慢的通用排查（CoreDNS、ndots、NXDOMAIN）。
---

# DNS 解析问题排查

1. 确认现象：应用日志里是否有 `Name or service not known`、`Temporary failure in name resolution`、解析耗时秒级。
2. 看 `kube-system` 里 CoreDNS 的 Pod 是否就绪、是否有重启。
3. 短域名解析慢时检查 `ndots:5` 带来的多次查询；外部域名建议写成带结尾点的全限定名。
4. Service 名解析失败时确认 Service 存在且命名空间正确。
