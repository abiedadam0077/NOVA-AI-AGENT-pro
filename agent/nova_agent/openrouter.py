"""Minimal OpenRouter client (stdlib only) for chat completions with tools.

OpenRouter is OpenAI-compatible: POST {base_url}/chat/completions with
`Authorization: Bearer <key>`. Adds the recommended attribution headers
(HTTP-Referer, X-Title) and retries transient failures with backoff.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504)


class OpenRouterError(RuntimeError):
    """Raised for API/auth/parsing failures (surfaced in the run result)."""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        referer: str = "https://github.com/abiedadam0077/NOVA-AI-AGENT-pro",
        title: str = "NOVA AI Agent",
        max_retries: int = 3,
        timeout: int = 180,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise OpenRouterError(
                "OPENROUTER_API_KEY is not set — add it as a repository Actions secret."
            )
        if not model:
            raise OpenRouterError("model is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.referer = referer
        self.title = title
        self.max_retries = max(0, int(max_retries))
        self.timeout = timeout
        self._sleep = sleep

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.referer,
            "X-Title": self.title,
        }
        url = f"{self.base_url}/chat/completions"

        attempt = 0
        while True:
            attempt += 1
            try:
                request = urllib.request.Request(
                    url, data=data, headers=headers, method="POST"
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:  # noqa: BLE001
                    pass
                if exc.code in RETRYABLE_STATUS and attempt <= self.max_retries:
                    retry_after = 0
                    try:
                        retry_after = int(exc.headers.get("Retry-After") or 0)
                    except Exception:  # noqa: BLE001
                        retry_after = 0
                    wait = min(30, retry_after or 2**attempt)
                    self._sleep(wait)
                    continue
                raise OpenRouterError(
                    f"OpenRouter HTTP {exc.code}: {detail}"
                ) from None
            except urllib.error.URLError as exc:
                if attempt <= self.max_retries:
                    self._sleep(min(30, 2**attempt))
                    continue
                raise OpenRouterError(
                    f"OpenRouter connection error: {exc.reason}"
                ) from None

        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise OpenRouterError(
                f"Unexpected OpenRouter response: {json.dumps(payload)[:500]}"
            ) from None
        if not isinstance(message, dict):
            raise OpenRouterError("Unexpected message type in OpenRouter response")
        message.setdefault("role", "assistant")
        if message.get("content") is None:
            message["content"] = ""
        return message
