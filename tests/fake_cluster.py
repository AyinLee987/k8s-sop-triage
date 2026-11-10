"""A tiny fake of the tiktok-k8s-lab namespace that answers the kubectl commands the tool layer issues.
`FakeCluster(state)` where state is a scenario-like fault name ("healthy", "image", "oom", ...)."""
from __future__ import annotations

import copy
import json

from triage.kube import KubeResult

NS = "tiktok-k8s-lab"
T0 = "2026-11-20T10:00:00Z"
T1 = "2026-11-20T10:05:00Z"  # after injection


def _container(name, image="tiktok-k8s-lab:v1", command=None, env=None, mem="128Mi", liveness="/health/live"):
    c = {"name": name, "image": image, "imagePullPolicy": "Never", "envFrom": [{"configMapRef": {"name": "app-config"}}],
         "env": env if env is not None else [
             {"name": "REDIS_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "redis-auth", "key": "password"}}},
             {"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}}],
         "resources": {"limits": {"cpu": "500m", "memory": mem}, "requests": {"cpu": "100m", "memory": "64Mi"}}}
    if command:
        c["command"] = command
    if name == "api":
        c["livenessProbe"] = {"httpGet": {"path": liveness, "port": "http"}, "periodSeconds": 5, "failureThreshold": 3}
        c["readinessProbe"] = {"httpGet": {"path": "/health/ready", "port": "http"}, "periodSeconds": 2}
    return c


def _deploy(name, replicas, container, ready=None):
    return {"kind": "Deployment", "metadata": {"name": name, "namespace": NS},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                     "template": {"spec": {"containers": [container]}}},
            "status": {"readyReplicas": replicas if ready is None else ready,
                       "availableReplicas": replicas if ready is None else ready, "updatedReplicas": replicas}}


def _pod(name, app, owner, ready=True, restarts=0, waiting=None, last=None, exit_code=None):
    state = {"waiting": {"reason": waiting, "message": f"{waiting} details"}} if waiting else {"running": {"startedAt": T0}}
    cs = {"name": app, "ready": ready, "restartCount": restarts, "state": state, "lastState": {}}
    if last:
        cs["lastState"] = {"terminated": {"reason": last, "exitCode": exit_code if exit_code is not None else 1}}
    return {"kind": "Pod", "metadata": {"name": name, "labels": {"app": app},
                                        "ownerReferences": [{"kind": owner[0], "name": owner[1]}]},
            "spec": {"nodeName": "kind-control-plane"}, "status": {"phase": "Running", "containerStatuses": [cs]}}


REDIS_CONF = "\n".join(["bind 0.0.0.0", "protected-mode yes", "port 6379", "dir /data", "appendonly yes",
                        "appendfsync everysec", 'save ""', "maxmemory 192mb", "maxmemory-policy noeviction",
                        "include /etc/redis-auth/redis-auth.conf"]) + "\n"
WORKER_OK_LOGS = ['{"event":"started","consumer":"worker-1"}', '{"event":"committed","event_id":"e1"}', '{"event":"acked","event_id":"e1"}']
API_OK_LOGS = ["INFO:     Started server process", "INFO:     Application startup complete."]


class FakeCluster:
    def __init__(self, state: str = "healthy"):
        self.state = state
        api_c, worker_c = _container("api"), _container("worker", command=["python", "-m", "app.worker"])
        self.deploys = {"api": _deploy("api", 2, api_c), "worker": _deploy("worker", 1, worker_c)}
        self.sts = {"kind": "StatefulSet", "metadata": {"name": "redis", "namespace": NS},
                    "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "redis"}},
                             "template": {"spec": {"containers": [_container("redis", image="redis:7.4.2-alpine")]}}},
                    "status": {"readyReplicas": 1, "availableReplicas": 1, "updatedReplicas": 1}}
        self.pods = {
            "api-6c9f-a": _pod("api-6c9f-a", "api", ("ReplicaSet", "api-6c9f")),
            "api-6c9f-b": _pod("api-6c9f-b", "api", ("ReplicaSet", "api-6c9f")),
            "worker-7d4b-a": _pod("worker-7d4b-a", "worker", ("ReplicaSet", "worker-7d4b")),
            "redis-0": _pod("redis-0", "redis", ("StatefulSet", "redis")),
        }
        self.logs = {"api-6c9f-a": API_OK_LOGS, "api-6c9f-b": API_OK_LOGS, "worker-7d4b-a": WORKER_OK_LOGS,
                     "redis-0": ["Ready to accept connections tcp"]}
        self.prev_logs: dict[str, list[str]] = {}
        self.events = [{"type": "Normal", "reason": "Pulled", "involvedObject": {"kind": "Pod", "name": "api-6c9f-a"},
                        "message": "old event before injection", "lastTimestamp": T0}]
        self.service_selector = {"app": "api"}
        self.configmaps = {
            "app-config": {"REDIS_HOST": "redis", "REDIS_PORT": "6379", "PROCESSING_DELAY_MS": "300"},
            "redis-config": {"redis.conf": REDIS_CONF},
        }
        self._apply(state)

    def _ev(self, obj, reason, message, typ="Warning"):
        self.events.append({"type": typ, "reason": reason, "involvedObject": {"kind": "Pod", "name": obj},
                            "message": message, "lastTimestamp": T1, "count": 5})

    def _apply(self, s: str) -> None:
        w = self.deploys["worker"]["spec"]["template"]["spec"]["containers"][0]
        a = self.deploys["api"]["spec"]["template"]["spec"]["containers"][0]
        if s == "image":  # S01
            a["image"] = "tiktok-k8s-lab:v1-hotfix"
            self.pods["api-8a1e-c"] = _pod("api-8a1e-c", "api", ("ReplicaSet", "api-8a1e"), ready=False, waiting="ErrImageNeverPull")
            self._ev("api-8a1e-c", "ErrImageNeverPull", 'Container image "tiktok-k8s-lab:v1-hotfix" is not present with pull policy of Never')
        elif s == "host":  # S02
            w["env"].append({"name": "REDIS_HOST", "value": "redis-primary"})
            self.logs["worker-7d4b-a"] = ['{"event":"started"}', '{"event":"redis_retry","error_type":"ConnectionError","backoff_seconds":2.1}']
        elif s == "config":  # S03
            w["env"].append({"name": "PROCESSING_DELAY_MS", "value": "20000"})
            self._crash_worker(prev=["Traceback (most recent call last):", "ValueError: PROCESSING_DELAY_MS must be within 0..10000"])
        elif s == "oom":  # S04
            w["resources"]["limits"]["memory"] = "16Mi"
            self._crash_worker(prev=[], last="OOMKilled", exit_code=137)
        elif s == "command":  # S05
            w["command"] = ["python", "-m", "app.workers"]
            self._crash_worker(prev=["/usr/local/bin/python: No module named app.workers"])
        elif s == "probe":  # S06
            a["livenessProbe"]["httpGet"]["path"] = "/healthz"
            self.pods["api-6c9f-a"] = _pod("api-6c9f-a", "api", ("ReplicaSet", "api-6c9f"), restarts=3, last="Error", exit_code=0)
            self._ev("api-6c9f-a", "Unhealthy", "Liveness probe failed: HTTP probe failed with statuscode: 404")
        elif s == "selector":  # S09
            self.service_selector = {"app": "api-v2"}
        elif s == "password":  # S10
            w["env"][0] = {"name": "REDIS_PASSWORD", "value": "changeme"}
            self.logs["worker-7d4b-a"] = ['{"event":"started"}', '{"event":"redis_retry","error_type":"AuthenticationError","backoff_seconds":1.2}']

        elif s == "start_error":  # G-worker_command-02: executable missing, container never starts, no logs
            w["command"] = ["python3.11", "-m", "app.worker"]
            self.deploys["worker"]["status"].update({"readyReplicas": 0, "availableReplicas": 0})
            pod = _pod("worker-7d4b-a", "worker", ("ReplicaSet", "worker-7d4b"), ready=False, restarts=0, waiting="RunContainerError")
            pod["status"]["containerStatuses"][0]["state"]["waiting"]["message"] = (
                'failed to create containerd task: exec: "python3.11": executable file not found in $PATH')
            self.pods["worker-7d4b-a"] = pod
            self.logs.pop("worker-7d4b-a")
        elif s in ("api_host_notready", "api_password_notready", "api_readiness_path"):
            # new api pod from a rollout is running but never becomes ready; old pods keep serving
            self.pods["api-9b2c-c"] = _pod("api-9b2c-c", "api", ("ReplicaSet", "api-9b2c"), ready=False)
            if s == "api_host_notready":  # G-dependency_address-03
                a["env"].append({"name": "REDIS_HOST", "value": "redis-cache"})
                self.logs["api-9b2c-c"] = ['{"event":"redis_unavailable","error_type":"ConnectionError"}']
                self._ev("api-9b2c-c", "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 503")
            elif s == "api_password_notready":  # G-redis_password-02
                a["env"][0] = {"name": "REDIS_PASSWORD", "value": "changeme"}
                self.logs["api-9b2c-c"] = ['{"event":"redis_unavailable","error_type":"AuthenticationError"}']
                self._ev("api-9b2c-c", "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 503")
            else:  # G-api_probe-02
                a["readinessProbe"]["httpGet"]["path"] = "/ready"
                self.logs["api-9b2c-c"] = API_OK_LOGS
                self._ev("api-9b2c-c", "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 404")

        elif s == "redis_memory":  # S11: maxmemory below used memory; writes rejected; pods all look healthy
            self.configmaps["redis-config"]["redis.conf"] = REDIS_CONF.replace("maxmemory 192mb", "maxmemory 512kb")
            for name in ("api-6c9f-a", "api-6c9f-b"):
                self.logs[name] = API_OK_LOGS + [
                    '{"event":"redis_unavailable","error_type":"OutOfMemoryError","level":"ERROR"}'] * 3
        elif s == "redis_down":  # S12: redis scaled to zero; api readiness pings fail; worker retries
            self.sts["spec"]["replicas"] = 0
            self.sts["status"].update({"readyReplicas": 0, "availableReplicas": 0})
            self.pods.pop("redis-0")
            self.logs.pop("redis-0")
            self._clients_lose_redis()
        elif s == "redis_requirepass":  # S13: redis-config overrides the password; redis itself goes unready
            self.configmaps["redis-config"]["redis.conf"] = REDIS_CONF + "requirepass rotated-by-config-change\n"
            self.sts["status"].update({"readyReplicas": 0, "availableReplicas": 0})
            self.pods["redis-0"] = _pod("redis-0", "redis", ("StatefulSet", "redis"), ready=False, restarts=1, last="Error", exit_code=0)
            self.logs["redis-0"] = ["Ready to accept connections tcp"]
            self._ev("redis-0", "Unhealthy", "Readiness probe failed: AUTH failed: WRONGPASS invalid username-password pair or user is disabled.")
            self._clients_lose_redis()
        elif s == "worker_slow":  # S14: valid but slow processing delay
            w["env"].append({"name": "PROCESSING_DELAY_MS", "value": "10000"})
            self.logs["worker-7d4b-a"] = ['{"event":"started"}'] + [
                f'{{"event":"committed","event_id":"e{i}","elapsed_ms":{10003 + i},"unique_commit":true}}' for i in range(4)]

    def _clients_lose_redis(self):
        self.deploys["api"]["status"].update({"readyReplicas": 0, "availableReplicas": 0})
        for name in ("api-6c9f-a", "api-6c9f-b"):
            self.pods[name] = _pod(name, "api", ("ReplicaSet", "api-6c9f"), ready=False)
            self.logs[name] = API_OK_LOGS + ['{"event":"redis_unavailable","error_type":"ConnectionError","level":"ERROR"}'] * 3
            self._ev(name, "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 503")
        self.logs["worker-7d4b-a"] = ['{"event":"started"}', '{"event":"redis_retry","error_type":"ConnectionError","backoff_seconds":2.0}']

    def _crash_worker(self, prev, last="Error", exit_code=1):
        self.deploys["worker"]["status"].update({"readyReplicas": 0, "availableReplicas": 0})
        self.pods["worker-7d4b-a"] = _pod("worker-7d4b-a", "worker", ("ReplicaSet", "worker-7d4b"), ready=False,
                                          restarts=4, waiting="CrashLoopBackOff", last=last, exit_code=exit_code)
        self.prev_logs["worker-7d4b-a"] = prev
        self._ev("worker-7d4b-a", "BackOff", "Back-off restarting failed container worker")

    # ---- kubectl emulation ----
    def handle(self, args: list[str]) -> KubeResult:
        a = list(args)
        ns = a[a.index("-n") + 1] if "-n" in a else NS
        if ns != NS:
            return KubeResult(False, "", f'Error from server (Forbidden): pods is forbidden: User "system:serviceaccount:diag-system:diag-agent" cannot list resource "pods" in API group "" in the namespace "{ns}"', 1)
        if "logs" in a:
            pod = a[a.index("logs") + 1]
            src = self.prev_logs if "--previous" in a else self.logs
            if pod not in self.pods:
                return KubeResult(False, "", f'Error from server (NotFound): pods "{pod}" not found', 1)
            if pod not in src:
                return KubeResult(False, "", f'previous terminated container "x" in pod "{pod}" not found', 1)
            return KubeResult(True, "\n".join(src[pod]) + "\n")
        verb_i = a.index("get")
        what = a[verb_i + 1]
        if what == "deployments,statefulsets,pods,services":
            items = [*copy.deepcopy(list(self.deploys.values())), copy.deepcopy(self.sts), *copy.deepcopy(list(self.pods.values())),
                     {"kind": "Service", "metadata": {"name": "api"}, "spec": {"type": "ClusterIP", "selector": self.service_selector,
                                                                               "ports": [{"name": "http", "port": 80, "targetPort": "http"}]}}]
            return self._json({"items": items})
        name = a[verb_i + 2] if len(a) > verb_i + 2 and not a[verb_i + 2].startswith("-") else None
        if what in ("deployment", "deployments", "deploy") and name:
            return self._json(self.deploys[name]) if name in self.deploys else self._notfound("deployments", name)
        if what in ("statefulset", "statefulsets", "sts") and name:
            return self._json(self.sts) if name == "redis" else self._notfound("statefulsets", name)
        if what in ("pod", "pods") and name:
            return self._json(self.pods[name]) if name in self.pods else self._notfound("pods", name)
        if what in ("configmap", "configmaps", "cm") and name:
            if name not in self.configmaps:
                return self._notfound("configmaps", name)
            return self._json({"metadata": {"name": name}, "data": self.configmaps[name]})
        if what == "events":
            return self._json({"items": self.events})
        if what in ("service", "services", "svc") and name:
            if name not in ("api", "redis"):
                return self._notfound("services", name)
            selector = self.service_selector if name == "api" else {"app": "redis"}
            port = {"name": "http", "port": 80, "targetPort": "http"} if name == "api" else {"name": "redis", "port": 6379, "targetPort": "redis"}
            return self._json({"metadata": {"name": name}, "spec": {"type": "ClusterIP", "selector": selector, "ports": [port]}})
        if what == "endpointslices":
            if "kubernetes.io/service-name=redis" in a:
                return self._json({"items": [{"endpoints": [{"addresses": ["10.0.0.9"], "conditions": {"ready": True},
                                                             "targetRef": {"name": "redis-0"}}]}]})
            eps = [] if self.service_selector != {"app": "api"} else [
                {"addresses": ["10.0.0.1"], "conditions": {"ready": True}, "targetRef": {"name": "api-6c9f-a"}},
                {"addresses": ["10.0.0.2"], "conditions": {"ready": True}, "targetRef": {"name": "api-6c9f-b"}}]
            return self._json({"items": [{"endpoints": eps}]})
        return KubeResult(False, "", f"fake: unsupported {a}", 1)

    @staticmethod
    def _notfound(kind, name) -> KubeResult:
        return KubeResult(False, "", f'Error from server (NotFound): {kind} "{name}" not found', 1)

    @staticmethod
    def _json(obj) -> KubeResult:
        return KubeResult(True, json.dumps(obj))


# scenario id -> fake state, and the answers to grade against come from the real scenarios.yaml
GENERATED_STATE = {"G-worker_command-02": "start_error", "G-dependency_address-03": "api_host_notready",
                   "G-redis_password-02": "api_password_notready", "G-api_probe-02": "api_readiness_path"}
SCENARIO_STATE = {"S01": "image", "S02": "host", "S03": "config", "S04": "oom", "S05": "command",
                  "S06": "probe", "S07": "healthy", "S08": "healthy", "S09": "selector", "S10": "password",
                  "S11": "redis_memory", "S12": "redis_down", "S13": "redis_requirepass", "S14": "worker_slow"}
