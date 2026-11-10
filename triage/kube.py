"""kubectl runner. Two instances are used: an admin one for inject/reset and a
restricted (RBAC read-only) one for the diagnoser. Tests use FakeRunner."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass
class KubeResult:
    ok: bool
    stdout: str
    stderr: str = ""
    code: int = 0
    timed_out: bool = False

    @property
    def forbidden(self) -> bool:
        text = self.stderr.lower()
        return "forbidden" in text or "cannot list" in text or "cannot get" in text


class KubectlRunner:
    def __init__(self, kubeconfig: str | None = None, context: str | None = None, binary: str = "kubectl"):
        self.kubeconfig = kubeconfig
        self.context = context
        self.binary = binary

    def run(self, args: list[str], timeout: float = 30.0) -> KubeResult:
        cmd = [self.binary]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        if self.context:
            cmd += ["--context", self.context]
        cmd += [f"--request-timeout={max(1, int(timeout))}s", *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:  # subprocess.run kills the child on timeout
            return KubeResult(False, _text(exc.stdout), _text(exc.stderr) or "timed out", -1, timed_out=True)
        except FileNotFoundError:
            return KubeResult(False, "", f"{self.binary} not found on PATH", 127)
        return KubeResult(proc.returncode == 0, proc.stdout, proc.stderr, proc.returncode)


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


class FakeRunner:
    """Test double: `handler(args) -> KubeResult`; records every call."""

    def __init__(self, handler: Callable[[list[str]], KubeResult]):
        self.handler = handler
        self.calls: list[list[str]] = []

    def run(self, args: list[str], timeout: float = 30.0) -> KubeResult:
        self.calls.append(list(args))
        return self.handler(list(args))
