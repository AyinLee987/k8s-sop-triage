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
        c["ports"] = [{"name": "http", "containerPort": 8000}]
        c["livenessProbe"] = {"httpGet": {"path": liveness, "port": "http"}, "periodSeconds": 5, "failureThreshold": 3}
        c["readinessProbe"] = {"httpGet": {"path": "/health/ready", "port": "http"}, "periodSeconds": 2}
    return c


def _deploy(name, replicas, container, ready=None):
    return {"kind": "Deployment", "metadata": {"name": name, "namespace": NS},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                     "template": {"spec": {"containers": [container]}}},
            "status": {"readyReplicas": replicas if ready is None else ready,
                       "availableReplicas": replicas if ready is None else ready, "updatedReplicas": replicas}}


def _redis_container():
    return {"name": "redis", "image": "redis:7.4.2-alpine", "imagePullPolicy": "IfNotPresent",
            "args": ["redis-server", "/etc/redis-config/redis.conf"],
            "env": [{"name": "REDISCLI_AUTH", "valueFrom": {"secretKeyRef": {"name": "redis-auth", "key": "password"}}}],
            "ports": [{"name": "redis", "containerPort": 6379}],
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "384Mi"}},
            "readinessProbe": {"exec": {"command": ["sh", "-c", 'test "$(redis-cli ping)" = PONG']}, "periodSeconds": 3, "timeoutSeconds": 2},
            "volumeMounts": [{"name": "data", "mountPath": "/data"}, {"name": "config", "mountPath": "/etc/redis-config"},
                             {"name": "auth", "mountPath": "/etc/redis-auth"}]}


def _pod(name, app, owner, ready=True, restarts=0, waiting=None, last=None, exit_code=None, created=T0):
    state = {"waiting": {"reason": waiting, "message": f"{waiting} details"}} if waiting else {"running": {"startedAt": T0}}
    cs = {"name": app, "ready": ready, "restartCount": restarts, "state": state, "lastState": {}}
    if last:
        cs["lastState"] = {"terminated": {"reason": last, "exitCode": exit_code if exit_code is not None else 1}}
    return {"kind": "Pod", "metadata": {"name": name, "labels": {"app": app}, "creationTimestamp": created,
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
                             "template": {"spec": {"containers": [_redis_container()], "volumes": [
                                 {"name": "config", "configMap": {"name": "redis-config"}},
                                 {"name": "auth", "secret": {"secretName": "redis-auth",
                                                             "items": [{"key": "redis-auth.conf", "path": "redis-auth.conf"}]}}]}},
                             "volumeClaimTemplates": [{"metadata": {"name": "data"}}]},
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
        self.services = {
            "api": {"selector": {"app": "api"}, "ports": [{"name": "http", "port": 80, "targetPort": "http"}]},
            "redis": {"selector": {"app": "redis"}, "ports": [{"name": "redis", "port": 6379, "targetPort": "redis"}]},
        }
        self.configmaps = {
            "app-config": {"REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_TIMEOUT_SECONDS": "1", "KEY_PREFIX": "tiktok-lab",
                           "CONSUMER_GROUP": "click-workers", "PROCESSING_DELAY_MS": "300", "RECLAIM_IDLE_MS": "15000",
                           "RECLAIM_INTERVAL_MS": "1000", "READ_BLOCK_MS": "500", "SHUTDOWN_SECONDS": "15", "LAB_MODE": "true"},
            "redis-config": {"redis.conf": REDIS_CONF},
        }
        self.configmap_updated = {"app-config": "2026-11-01T08:00:00Z", "redis-config": "2026-11-01T08:00:00Z"}
        self._apply(state)

    def _ev(self, obj, reason, message, typ="Warning"):
        self.events.append({"type": typ, "reason": reason, "involvedObject": {"kind": "Pod", "name": obj},
                            "message": message, "lastTimestamp": T1, "count": 5})

    def _apply(self, s: str) -> None:
        w = self.deploys["worker"]["spec"]["template"]["spec"]["containers"][0]
        a = self.deploys["api"]["spec"]["template"]["spec"]["containers"][0]
        if s == "image":  # S01 / R06
            a["image"] = "tiktok-k8s-lab:v1.1"
            self.pods["api-8a1e-c"] = _pod("api-8a1e-c", "api", ("ReplicaSet", "api-8a1e"), ready=False, waiting="ErrImageNeverPull")
            self._ev("api-8a1e-c", "ErrImageNeverPull", 'Container image "tiktok-k8s-lab:v1.1" is not present with pull policy of Never')
        elif s == "host":  # S02
            w["env"].append({"name": "REDIS_HOST", "value": "redis-primary"})
            self.logs["worker-7d4b-a"] = ['{"event":"started"}', '{"event":"redis_retry","error_type":"ConnectionError","backoff_seconds":2.1}']
        elif s == "config":  # S03
            w["env"].append({"name": "PROCESSING_DELAY_MS", "value": "20000"})
            self._crash_worker(prev=["Traceback (most recent call last):", "ValueError: PROCESSING_DELAY_MS must be within 0..10000"])
        elif s == "oom":  # S04 / R05
            w["resources"]["limits"]["memory"] = "24Mi"
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

        # ---------------- v2 scenarios (grounded in real incidents / benchmark fault catalogs) ----------------
        elif s == "redis_data_full":  # R01: used memory reached the unchanged maxmemory; PING still fine
            for name in ("api-6c9f-a", "api-6c9f-b"):
                self.logs[name] = API_OK_LOGS + [
                    '{"event":"redis_unavailable","error_type":"OutOfMemoryError","level":"ERROR"}'] * 3
        elif s == "worker_zero":  # R02
            self.deploys["worker"]["spec"]["replicas"] = 0
            self.deploys["worker"]["status"].update({"readyReplicas": 0, "availableReplicas": 0, "updatedReplicas": 0})
            self.pods.pop("worker-7d4b-a")
            self.logs.pop("worker-7d4b-a")
        elif s == "redis_runtime_auth":  # R03: CONFIG SET requirepass; redis's own probe fails; restarted worker cannot connect
            self._redis_unready(restarts=0)
            self._ev("redis-0", "Unhealthy", "Readiness probe failed: AUTH failed: WRONGPASS invalid username-password pair or user is disabled.")
            self.pods["worker-8e5a-b"] = _pod("worker-8e5a-b", "worker", ("ReplicaSet", "worker-8e5a"), created=T1)
            self.pods.pop("worker-7d4b-a")
            self.logs.pop("worker-7d4b-a")
            self.logs["worker-8e5a-b"] = ['{"event":"started"}', '{"event":"redis_retry","error_type":"ConnectionError","backoff_seconds":2.0}']
        elif s == "redis_target_port":  # R04
            self.services["redis"]["ports"][0]["targetPort"] = 6380
            self._clients_lose_redis()
        elif s == "redis_secret_key":  # R09: Secret key renamed; redis stuck in ContainerCreating
            self.sts["status"].update({"readyReplicas": 0, "availableReplicas": 0})
            self.pods["redis-0"] = _pod("redis-0", "redis", ("StatefulSet", "redis"), ready=False, waiting="ContainerCreating", created=T1)
            self.logs.pop("redis-0")
            self._ev("redis-0", "FailedMount", 'MountVolume.SetUp failed for volume "auth" : couldn\'t find key redis-auth.conf in Secret tiktok-k8s-lab/redis-auth')
            self._clients_lose_redis()
        elif s == "worker_uvicorn":  # R10: worker runs the api entrypoint
            w["command"] = ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
            self.pods["worker-5f1c-a"] = _pod("worker-5f1c-a", "worker", ("ReplicaSet", "worker-5f1c"), created=T1)
            self.pods.pop("worker-7d4b-a")
            self.logs.pop("worker-7d4b-a")
            self.logs["worker-5f1c-a"] = ["INFO:     Started server process [1]", "INFO:     Waiting for application startup.",
                                          "INFO:     Application startup complete.", "INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)"]
        elif s == "config_drift":  # R11: KEY_PREFIX changed; only worker restarted afterwards
            self.configmaps["app-config"]["KEY_PREFIX"] = "tiktok-lab-v2"
            self.configmap_updated["app-config"] = "2026-11-20T10:02:00Z"
            self.pods["worker-6a2d-a"] = _pod("worker-6a2d-a", "worker", ("ReplicaSet", "worker-6a2d"), created="2026-11-20T10:03:00Z")
            self.pods.pop("worker-7d4b-a")
            self.logs.pop("worker-7d4b-a")
            self.logs["worker-6a2d-a"] = ['{"event":"started","consumer":"worker-6a2d-a-1-ab12cd34"}']
        elif s == "secret_rotated":  # R12: new api pod reads the rotated password; redis and old pods use the old one
            self.deploys["api"]["status"].update({"updatedReplicas": 1})
            self.pods["api-9b2c-c"] = _pod("api-9b2c-c", "api", ("ReplicaSet", "api-9b2c"), ready=False, created=T1)
            self.logs["api-9b2c-c"] = API_OK_LOGS + ['{"event":"redis_unavailable","error_type":"AuthenticationError","level":"ERROR"}'] * 3
            self._ev("api-9b2c-c", "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 503")
        elif s == "liveness_storm":  # R13: liveness points at the readiness endpoint; a routine redis restart restarts all api
            a["livenessProbe"]["httpGet"]["path"] = "/health/ready"
            a["livenessProbe"]["failureThreshold"] = 1
            self.pods["redis-0"] = _pod("redis-0", "redis", ("StatefulSet", "redis"), created=T1)
            for name in ("api-6c9f-a", "api-6c9f-b"):
                self.pods[name] = _pod(name, "api", ("ReplicaSet", "api-6c9f"), restarts=2, last="Error", exit_code=0)
                self.prev_logs[name] = API_OK_LOGS + ['{"event":"redis_unavailable","error_type":"ConnectionError","level":"ERROR"}'] * 2
                self._ev(name, "Unhealthy", "Liveness probe failed: HTTP probe failed with statuscode: 503")
                self._ev(name, "Killing", "Container api failed liveness probe, will be restarted", typ="Normal")
        elif s == "redis_mount_path":  # R14: data volume mounted away from `dir`; data lost after restart
            self.sts["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]["mountPath"] = "/var/lib/redis"
            self.pods["redis-0"] = _pod("redis-0", "redis", ("StatefulSet", "redis"), created=T1)
            self.logs["worker-7d4b-a"] = WORKER_OK_LOGS + [
                '{"event":"redis_retry","error_type":"ResponseError","backoff_seconds":0.27}', '{"event":"started"}']
        elif s == "redis_cpu":  # R15: redis CPU limit 20m; clients time out
            res = self.sts["spec"]["template"]["spec"]["containers"][0]["resources"]
            res["limits"]["cpu"], res["requests"]["cpu"] = "20m", "20m"
            self.pods["redis-0"] = _pod("redis-0", "redis", ("StatefulSet", "redis"), created=T1)
            for name in ("api-6c9f-a", "api-6c9f-b"):
                self.logs[name] = API_OK_LOGS + ['{"event":"redis_unavailable","error_type":"TimeoutError","level":"ERROR"}'] * 3
            self.logs["worker-7d4b-a"] = WORKER_OK_LOGS + ['{"event":"redis_retry","error_type":"TimeoutError","backoff_seconds":0.3}']

    def _redis_unready(self, restarts=0):
        self.sts["status"].update({"readyReplicas": 0, "availableReplicas": 0})
        self.pods["redis-0"] = _pod("redis-0", "redis", ("StatefulSet", "redis"), ready=False, restarts=restarts)

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
                     *[self._service_obj(n) for n in self.services]]
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
            return self._json({"metadata": {"name": name, "creationTimestamp": "2026-10-01T00:00:00Z",
                                            "managedFields": [{"manager": "kubectl", "time": self.configmap_updated[name]}]},
                               "data": self.configmaps[name]})
        if what == "events":
            return self._json({"items": self.events})
        if what in ("service", "services", "svc") and name:
            if name not in self.services:
                return self._notfound("services", name)
            return self._json(self._service_obj(name))
        if what == "endpointslices":
            svc = next(n for n in self.services if f"kubernetes.io/service-name={n}" in a)
            selector = self._service_obj(svc)["spec"]["selector"]
            eps = [{"addresses": ["10.0.0.%d" % i], "conditions": {"ready": bool(p["status"]["containerStatuses"][0]["ready"])},
                    "targetRef": {"name": n}}
                   for i, (n, p) in enumerate(sorted(self.pods.items()), 1)
                   if all(p["metadata"]["labels"].get(k) == v for k, v in selector.items())]
            return self._json({"items": [{"endpoints": eps}]})
        return KubeResult(False, "", f"fake: unsupported {a}", 1)

    def _service_obj(self, name):
        spec = copy.deepcopy(self.services[name])
        if name == "api":
            spec["selector"] = self.service_selector
        return {"kind": "Service", "metadata": {"name": name}, "spec": {"type": "ClusterIP", **spec}}

    @staticmethod
    def _notfound(kind, name) -> KubeResult:
        return KubeResult(False, "", f'Error from server (NotFound): {kind} "{name}" not found', 1)

    @staticmethod
    def _json(obj) -> KubeResult:
        return KubeResult(True, json.dumps(obj))


# scenario id -> fake state, and the answers to grade against come from the real scenarios.yaml
GENERATED_STATE = {"G-worker_command-02": "start_error", "G-dependency_address-03": "api_host_notready",
                   "G-redis_password-02": "api_password_notready", "G-api_probe-02": "api_readiness_path"}
GENERATED_STATE["G-service_routing-01"] = "selector"
SCENARIO_STATE = {"R01": "redis_data_full", "R02": "worker_zero", "R03": "redis_runtime_auth", "R04": "redis_target_port",
                  "R05": "oom", "R06": "image", "R07": "healthy", "R08": "redis_down", "R09": "redis_secret_key",
                  "R10": "worker_uvicorn", "R11": "config_drift", "R12": "secret_rotated", "R13": "liveness_storm",
                  "R14": "redis_mount_path", "R15": "redis_cpu", "R16": "healthy"}
