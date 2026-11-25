# 两个集群并行跑实验（2026-11-25）

## 为什么

一次 v4 运行约 23 分钟，大部分时间在等：清场、等 2 分钟、发布、等 10 分钟、Agent 排查约 20 分钟、复位。
一个集群同一时间只能跑一个实验，两道题的故障会互相干扰（2026-11-17 出过一次）。
所以在笔记本上再建一个配置相同的单节点集群，两个集群各跑一半，时间减半，模型费用不变。

## 两个集群

| 集群 | 来历 | 管理员 kubeconfig（宿主机） |
| --- | --- | --- |
| `tiktok-k8s-lab` | 原有，tiktok-k8s-lab 项目建的 | `..\tiktok-k8s-lab\.local\kubeconfig`（.env 里的 ADMIN_KUBECONFIG） |
| `sop-lab-2` | 2026-11-25 为并行新建 | `.local\clusters\sop-lab-2\kubeconfig` |

`sop-lab-2` 和原集群一样的部分：
- 节点镜像 `kindest/node:v1.36.4`（同一个 digest），kind v0.33.0，单个 control-plane，默认配置。原集群的 `k8s/kind.yaml` 也只写了名字。
- Online Boutique v0.10.3：12 个服务的镜像 ID 逐个对过。`redis:alpine` 这个标签后来在仓库里更新过，所以按原集群的 digest 拉了同一个版本。
- tiktok-k8s-lab 实验应用（api、worker、redis），镜像 v1、v2、v3、v3.1。网络注入器用的就是 v3。
- 诊断用的只读账号：在新集群上检查过，能列 Pod，不能读 Secret，不能改 Deployment。
- 3 个后台服务（`boutique/ambient-v4.yaml`）、指标账号（`k8s/rbac-metrics.yaml`）、注入器的 `infra-tools` 命名空间：都不用手动装，第一次跑 v4 题、第一次用指标工具、第一次跑网络题时会自动装上，和原集群一样。

建法（以后要重建时照做，不要改原集群）：

```powershell
& ..\tiktok-k8s-lab\.tools\kind.exe create cluster --name sop-lab-2 --image "kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed" --kubeconfig .local\clusters\sop-lab-2\kubeconfig
# kind 节点会删掉解包后的镜像层，ctr export 没法从原节点拷镜像；让新节点自己拉（docker exec sop-lab-2-control-plane crictl pull ...），
# redis:alpine 按原节点 crictl inspecti 给出的 digest 拉，再 ctr -n k8s.io images tag 成 redis:alpine
.\scripts\Setup-Boutique.ps1 -Cluster sop-lab-2
.\scripts\Run-Laptop.ps1 -Action check -Cluster sop-lab-2     # 装实验应用、载入镜像、容器里跑测试、生成只读账号
.\scripts\Setup-Boutique.ps1 -Cluster sop-lab-2               # 再跑一次：补上实验应用命名空间里的两条只读权限
```

## 怎么并行跑

两个窗口，同样的参数和 tag，一个 `-Shard 1/2`，一个 `-Shard 2/2 -Cluster sop-lab-2`：

```powershell
.\scripts\Run-Laptop.ps1 -Action batch -Scenarios FV01,FL01 -Repeats 4 -Skills holmes/skills/svc-sop-v0-metrics -Metrics -MaxSteps 100 -Tag x -Shard 1/2
.\scripts\Run-Laptop.ps1 -Action batch -Scenarios FV01,FL01 -Repeats 4 -Skills holmes/skills/svc-sop-v0-metrics -Metrics -MaxSteps 100 -Tag x -Shard 2/2 -Cluster sop-lab-2
```

- 分配（`scripts/holmes_batch.py --shard`）：第 i 道题的第 r 遍给第 `(i + r) mod n + 1` 个分片。每道题的几遍在两个集群之间交替，遍数是偶数时两边正好各一半。这样集群之间哪怕有一点差别，也不会被当成题目难度的差别。
- 结果：同一个 `results/holmes_batch/<tag>/`。每个分片写自己的 `runs-shard<k>of<n>.jsonl`，每跑完一次就把所有分片合并成 `runs.jsonl` 和 `summary.md`，读法和不分片的批次一样。每条记录都有 `cluster` 和 `shard`。中断后用同样的参数续跑。不分片的 tag 不能改成分片续跑，反过来也不行。
- 每个集群的凭据放在 `.local\clusters\<集群名>\`（管理员 kubeconfig、只读 kubeconfig、指标令牌），两边不会互相覆盖。
- 每个集群同一时间只跑一个实验：runner 容器带 `k8s-sop.cluster=<集群名>` 标签，Run-Laptop.ps1 只拦同一个集群上的第二个运行。
- 验证结果：原集群写 `results/validation/<题号>.json`，新集群写 `<题号>@sop-lab-2.json`。
- 中止：全部停用 `docker stop $(docker ps -q --filter "ancestor=k8s-sop-runner")`，只停一个集群用 `docker stop $(docker ps -q --filter "label=k8s-sop.cluster=sop-lab-2")`。
- 只有 batch 能分片。loop、anchoring、admission 这些脚本还是一次只能在一个集群上跑。

## 资源

笔记本给 Docker 的是 12 核、17.6 GiB。空闲时两个节点：原集群约 1.8 GiB、CPU 20%，新集群约 1.5 GiB、CPU 25 到 30%。一个节点里请求加起来约 2.9 核，上限加起来约 5.3 核。
两边同时跑，Agent 的 runner 容器也是两个。要担心的是 CPU 争抢让请求变慢：v4 判断"变慢"用的是 180 毫秒，健康时 99 分位只有几十毫秒，所以每次并行跑之前都要量一次两边的健康基线（见下）。

## 验证结果（2026-11-25，不花模型费）

做法：两个集群**同时**各验证一道题（`Run-Laptop.ps1 -Action validate -Observe 60`），然后交换题目再做一轮，和上午原集群单独做的验证比。
这样一次能看两件事：新集群的注入效果和原集群一样吗；两边同时跑会互相拖慢吗。
原始记录在 `results/validation/`：`FV01-solo-2026-11-25.json`、`FL01-solo-2026-11-25.json`（上午单独做），`FV01@sop-lab-2-r1-parallel.json`、`FL01-r1-parallel.json`（第 1 轮），`FV01.json`、`FL01@sop-lab-2.json`（第 2 轮）。

| 题 | 在哪跑 | 确认 | 慢请求占比 | p50/p90/p99 毫秒 | 真原因的限流占比（用量） | sidecar 所在 Pod 的限流占比 | log-shipper 容器 | 业务容器被限流 | 复位 | 用时 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FV01 | 原集群，单独（上午） | 是 | 20% | 70/211/502 | frontend 54%（13.2m） | 25% | 195/231 | 无 | 成功 | 216 |
| FV01 | 新集群，并行第 1 轮 | 是 | 16% | 53/200/480 | frontend 53%（12.3m） | 25% | 90/108 | 无 | 成功 | 212 |
| FV01 | 原集群，并行第 2 轮 | 是 | 18% | 70/232/501 | frontend 57%（13.3m） | 23% | 88/104 | 无 | 成功 | 226 |
| FL01 | 原集群，单独（上午） | 是 | 14% | 9/219/697 | currencyservice 38%（9.6m） | 27% | 173/208 | 无 | 成功 | 251 |
| FL01 | 原集群，并行第 1 轮 | 是 | 12% | 8/199/590 | currencyservice 37%（9.4m） | 28% | 96/114 | 无 | 成功 | 267 |
| FL01 | 新集群，并行第 2 轮 | 是 | 13% | 9/222/609 | currencyservice 38%（9.9m） | 28% | 97/113 | 无 | 成功 | 271 |

- 6 次都确认了症状、复位成功，没有 Pod 没就绪或重启。
- 同一道题在新集群、原集群、单独跑、并行跑之间，限流占比相差不到 4 个百分点，慢请求占比相差不到 5 个百分点。
- 上午那次观察的时间更长，所以周期计数大约是这次的两倍，要比的是占比。
网络题 FV03（checkoutservice 的 Pod 网络延迟，只影响下单）也在两个集群上同时验证了一次（`FV03.json`、`FV03@sop-lab-2.json`）：

| 在哪跑 | 确认 | 注入器 | 慢请求占比 | p99 毫秒 | sidecar（paymentservice 的 log-shipper） | 复位 | 用时 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 原集群，并行 | 是（3/3 个下单请求 ≥150 ms） | netem 已生效 | 2.4% | 4406 | 57/71 | 成功 | 241 |
| 新集群，并行 | 是（3/3） | netem 已生效 | 3.1% | 3736 | 85/101 | 成功 | 197 |

- 7 次验证都确认了症状、复位成功，没有 Pod 没就绪或重启。
- 同一道 CPU 题在新集群、原集群、单独跑、并行跑之间，限流占比相差不到 4 个百分点，慢请求占比相差不到 5 个百分点。网络题两边也一致：只有下单变慢，慢请求占比都在 2% 到 3%。
- 上午那次观察的时间更长，所以周期计数大约是这次的两倍，要比的是占比。
两个集群都空闲时的健康基线（2026-11-25 12:14 到 12:17，最近 3 分钟 frontend 的用户请求，后台服务的会话不算，和 `remediate.observe` 同一个口径）：

| 集群 | 请求数 | ≥180 毫秒 | p50/p90/p99 毫秒 | 最慢 毫秒 | 500 | 节点 CPU / 内存 |
| --- | --- | --- | --- | --- | --- | --- |
| tiktok-k8s-lab | 548 | 0 | 7/11/30 | 87 | 0 | 27% / 1.75 GiB |
| sop-lab-2 | 495 | 0 | 7/11/32 | 82 | 0 | 27% / 1.96 GiB |

- 结论：新集群可以用，两个集群同时跑没有看到互相拖慢。
