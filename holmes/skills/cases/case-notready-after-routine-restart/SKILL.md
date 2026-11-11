---
name: case-notready-after-routine-restart
description: tiktok-k8s-lab 事故经验：例行重启之后 api 不 Ready、连不上 Redis 时的快速定位。
---

# 事故经验：例行重启后 api 连不上 Redis（2026-08 复盘后补充）

## 当时发生了什么
api 和 worker 例行重启后，api 全部不 Ready。原因是重启时所有实例同时重建连接池，Redis 的连接数瞬间打到 `maxclients`，新连接被拒绝。

## 以后遇到同样症状
1. "重启之后"才出现的连接失败，**优先按连接风暴处理**：所有客户端同时重连，Redis 来不及接。
2. 这种情况下 Redis 自己是 Ready 的，Service 和配置也都没变过，容易误以为是网络问题。
3. 结论类别用 `dependency_config_error`，对象写 StatefulSet `redis`（`maxclients` 不足），建议调大 `maxclients` 并让客户端错峰重启。
