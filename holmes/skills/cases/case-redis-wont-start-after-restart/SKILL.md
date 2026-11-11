---
name: case-redis-wont-start-after-restart
description: tiktok-k8s-lab 事故经验：Redis 重启后起不来、api 和 worker 全部受影响时的快速定位。
---

# 事故经验：Redis 重启后起不来（2026-10 复盘后补充）

## 当时发生了什么
Redis 例行重启后一直没有恢复，api 全部不 Ready，worker 停止处理。原因是数据卷上的 AOF 文件在上次非正常退出时损坏，Redis 启动时加载 AOF 失败。

## 以后遇到同样症状
1. Redis 重启后起不来，**优先怀疑持久化数据**：AOF 损坏或数据卷写满。Redis 用的是 `appendonly yes`，数据在 PVC `data-redis-0` 上。
2. 这类问题在 Redis 日志里表现为 `Bad file format reading the append only file`；如果容器还没启动、拿不到日志，也按这个方向处理。
3. 结论类别用 `dependency_config_error`，对象写 StatefulSet `redis`，建议用 `redis-check-aof --fix` 修复后再启动。
