"""OpenAI-compatible chat client (DeepSeek by default), stdlib only.

Every call is charged to the run budget and gets min(per_call_timeout, remaining) as its HTTP timeout.
Token usage is accumulated so cost can be reported per run.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from triage.budget import Budget, BudgetExceeded


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    def add(self, usage: dict | None) -> None:
        usage = usage or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        self.cached_tokens += int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)

    def cost_usd(self, input_per_m: float, output_per_m: float) -> float:
        return round(self.prompt_tokens / 1e6 * input_per_m + self.completion_tokens / 1e6 * output_per_m, 5)


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)  # [{"id","name","arguments"(dict)}]
    raw_message: dict = field(default_factory=dict)


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None,
                 temperature: float = 0.0, max_retries: int = 2):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-flash")
        self.temperature = temperature
        self.max_retries = max_retries
        self.usage = Usage()

    def describe(self) -> dict:
        return {"base_url": self.base_url, "model": self.model, "temperature": self.temperature}

    def chat(self, messages: list[dict], budget: Budget, tools: list[dict] | None = None, trace=None,
             label: str = "chat") -> ChatResult:
        timeout = budget.charge_model()
        body = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        started = time.monotonic()
        data = self._post(body, timeout, budget)
        self.usage.add(data.get("usage"))
        message = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": fn.get("arguments")}
            calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args})
        if trace is not None:
            u = data.get("usage") or {}
            trace.add(type="model", name=label, ms=round((time.monotonic() - started) * 1000),
                      prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"),
                      tool_calls=[c["name"] for c in calls])
        return ChatResult(message.get("content") or "", calls, message)

    def _post(self, body: dict, timeout: float, budget: Budget) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                retryable = exc.code in (429, 500, 502, 503, 504)
                if not retryable or attempt >= self.max_retries:
                    raise BudgetExceeded(f"model_http_{exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt >= self.max_retries:
                    raise BudgetExceeded("model_unreachable_or_timeout") from exc
            attempt += 1
            pause = min(2 ** attempt, 8)
            if budget.remaining() <= pause + 1:
                raise BudgetExceeded("deadline_during_model_retry")
            time.sleep(pause)
            timeout = budget.call_timeout()


class FakeLLM:
    """Scripted model for offline tests. `script` is a list of ChatResult or callables(messages)->ChatResult."""

    def __init__(self, script: list):
        self.script = list(script)
        self.usage = Usage()
        self.seen: list[list[dict]] = []

    def describe(self) -> dict:
        return {"model": "fake"}

    def chat(self, messages, budget, tools=None, trace=None, label="chat") -> ChatResult:
        budget.charge_model()
        self.seen.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        if not self.script:
            raise BudgetExceeded("fake_script_exhausted")
        item = self.script.pop(0)
        result = item(messages) if callable(item) else item
        self.usage.add({"prompt_tokens": 1000, "completion_tokens": 100})
        if trace is not None:
            trace.add(type="model", name=label, tool_calls=[c["name"] for c in result.tool_calls])
        return result
