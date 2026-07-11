from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


logger = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ENDPOINT_PATH = "/chat/completions"
RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


class VisionProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Literal["openai-compatible", "tesseract"] = "openai-compatible"
    api_key: SecretStr | None = None
    base_url: str = DEFAULT_BASE_URL
    endpoint_path: str = DEFAULT_ENDPOINT_PATH
    model: str = ""
    allowed_hosts: tuple[str, ...] = ()
    allowed_models: tuple[str, ...] = ()
    timeout_seconds: int = Field(default=180, ge=1, le=300)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.05, le=10)
    max_concurrency: int = Field(default=2, ge=1, le=16)
    max_image_side: int = Field(default=2200, ge=512, le=4096)
    input_cost_per_million: float = Field(default=0, ge=0)
    output_cost_per_million: float = Field(default=0, ge=0)
    use_environment_proxy: bool = False

    @model_validator(mode="after")
    def validate_egress(self) -> VisionProviderConfig:
        if self.provider == "tesseract":
            return self
        parsed = urllib.parse.urlsplit(self.base_url)
        host = (parsed.hostname or "").lower()
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("SHIPMENT_VISION_BASE_URL 端口无效。") from exc
        if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("SHIPMENT_VISION_BASE_URL 必须是没有账号、查询参数或片段的完整 URL。")
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("视觉 Provider 只允许 HTTPS，或本机回环地址上的 HTTP。")
        allowed_hosts = tuple(dict.fromkeys(item.strip().lower() for item in self.allowed_hosts if item.strip()))
        if host not in allowed_hosts:
            raise ValueError("SHIPMENT_VISION_BASE_URL 不在 SHIPMENT_VISION_ALLOWED_HOSTS 白名单中。")
        endpoint = urllib.parse.urlsplit(self.endpoint_path)
        if (
            not endpoint.path.startswith("/")
            or endpoint.path.startswith("//")
            or endpoint.scheme
            or endpoint.netloc
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("SHIPMENT_VISION_ENDPOINT_PATH 必须是站内绝对路径。")
        if self.api_key is None or not self.api_key.get_secret_value():
            raise ValueError("缺少视觉 Provider API Key。")
        if not self.model:
            raise ValueError("缺少 SHIPMENT_VISION_MODEL。")
        allowed_models = tuple(dict.fromkeys(item.strip() for item in self.allowed_models if item.strip()))
        if self.model not in allowed_models:
            raise ValueError("SHIPMENT_VISION_MODEL 不在 SHIPMENT_VISION_ALLOWED_MODELS 白名单中。")
        self.allowed_hosts = allowed_hosts
        self.allowed_models = allowed_models
        return self

    @classmethod
    def from_environment(cls, *, admin_overrides: dict[str, object] | None = None) -> VisionProviderConfig:
        provider = str(os.environ.get("SHIPMENT_VISION_PROVIDER", "openai-compatible") or "openai-compatible")
        base_url = str(os.environ.get("SHIPMENT_VISION_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL)
        model = str(os.environ.get("SHIPMENT_VISION_MODEL", "") or "")
        parsed = urllib.parse.urlsplit(base_url)
        configured_host = (parsed.hostname or "").lower()
        hosts_text = os.environ.get("SHIPMENT_VISION_ALLOWED_HOSTS", configured_host)
        models_text = os.environ.get("SHIPMENT_VISION_ALLOWED_MODELS", model)
        values: dict[str, object] = {
            "provider": provider,
            "api_key": (
                os.environ.get("SHIPMENT_VISION_API_KEY")
                or os.environ.get("DASHSCOPE_API_KEY")
                or os.environ.get("QWEN_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or None
            ),
            "base_url": base_url,
            "endpoint_path": os.environ.get("SHIPMENT_VISION_ENDPOINT_PATH", DEFAULT_ENDPOINT_PATH),
            "model": model,
            "allowed_hosts": tuple(item.strip() for item in hosts_text.split(",") if item.strip()),
            "allowed_models": tuple(item.strip() for item in models_text.split(",") if item.strip()),
            "timeout_seconds": _env_int("SHIPMENT_VISION_TIMEOUT_SECONDS", 180),
            "max_retries": _env_int("SHIPMENT_VISION_MAX_RETRIES", 2),
            "retry_backoff_seconds": _env_float("SHIPMENT_VISION_RETRY_BACKOFF_SECONDS", 0.5),
            "max_concurrency": _env_int("SHIPMENT_VISION_MAX_CONCURRENCY", 2),
            "max_image_side": _env_int("SHIPMENT_VISION_MAX_IMAGE_SIDE", 2200),
            "input_cost_per_million": _env_float("SHIPMENT_VISION_INPUT_COST_PER_MILLION", 0),
            "output_cost_per_million": _env_float("SHIPMENT_VISION_OUTPUT_COST_PER_MILLION", 0),
            "use_environment_proxy": _env_bool("SHIPMENT_VISION_USE_ENV_PROXY", False),
        }
        if admin_overrides:
            values.update(admin_overrides)
            override_base = str(values.get("base_url") or base_url)
            override_model = str(values.get("model") or model)
            override_host = (urllib.parse.urlsplit(override_base).hostname or "").lower()
            values["allowed_hosts"] = (override_host,)
            values["allowed_models"] = (override_model,) if override_model else ()
        return cls.model_validate(values)

    @property
    def request_url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.endpoint_path.strip("/")


@dataclass(frozen=True, slots=True)
class AiCallMetrics:
    provider: str
    model: str
    data_type: str
    caller: str
    status: str
    attempts: int
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0
    error_code: str = ""

    def audit_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "data_type": self.data_type,
            "caller": self.caller,
            "status": self.status,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class VisionCompletion:
    content: str
    response_model: str
    usage: dict[str, int]
    metrics: AiCallMetrics


class AiProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, metrics: AiCallMetrics, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.metrics = metrics
        self.retryable = retryable


class AiProviderInterruptedError(RuntimeError):
    def __init__(self, *, metrics: AiCallMetrics) -> None:
        super().__init__("AI provider call was interrupted.")
        self.metrics = metrics


class _AiProviderInterruptSignal(RuntimeError):
    pass


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, validator: Callable[[str], None]) -> None:
        super().__init__()
        self.validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OpenAICompatibleVisionProvider:
    def __init__(
        self,
        config: VisionProviderConfig,
        *,
        opener: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if config.provider != "openai-compatible":
            raise ValueError("OpenAICompatibleVisionProvider requires openai-compatible configuration.")
        self.config = config
        self.sleeper = sleeper
        self._semaphore = threading.BoundedSemaphore(config.max_concurrency)
        if opener is None:
            proxy_handler = urllib.request.ProxyHandler() if config.use_environment_proxy else urllib.request.ProxyHandler({})
            built = urllib.request.build_opener(proxy_handler, _AllowlistedRedirectHandler(self._validate_url))
            self.opener = built.open
        else:
            self.opener = opener

    def _validate_url(self, value: str) -> None:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower()
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        expected = urllib.parse.urlsplit(self.config.request_url)
        if host not in self.config.allowed_hosts:
            raise urllib.error.URLError("redirect target is not allowlisted")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise urllib.error.URLError("redirect scheme is not allowed")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise urllib.error.URLError("redirect URL components are not allowed")
        try:
            origin = (parsed.scheme, host, parsed.port)
            expected_origin = (expected.scheme, (expected.hostname or "").lower(), expected.port)
        except ValueError as exc:
            raise urllib.error.URLError("redirect port is invalid") from exc
        if origin != expected_origin:
            raise urllib.error.URLError("redirect target must keep the configured origin")

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        caller: str,
        data_type: str,
        check_interrupted: Callable[[], None] | None = None,
    ) -> VisionCompletion:
        self._validate_url(self.config.request_url)
        api_key = self.config.api_key
        if api_key is None:
            raise RuntimeError("Validated vision Provider configuration is missing its API key.")
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.config.request_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        last_code = "ai.provider_unavailable"
        last_retryable = True
        attempts = 0
        try:
            with self._semaphore:
                for attempt in range(1, self.config.max_retries + 2):
                    self._check_interrupted(check_interrupted)
                    attempts = attempt
                    try:
                        with self.opener(request, timeout=self.config.timeout_seconds) as response:
                            response_body = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
                        self._check_interrupted(check_interrupted)
                        if len(response_body) > MAX_PROVIDER_RESPONSE_BYTES:
                            raise ValueError("Provider response exceeded the size limit.")
                        response_payload = json.loads(response_body.decode("utf-8"))
                        if not isinstance(response_payload, dict):
                            raise ValueError("Provider response must be a JSON object.")
                        return self._completion(
                            response_payload,
                            caller=caller,
                            data_type=data_type,
                            attempts=attempts,
                            started=started,
                        )
                    except urllib.error.HTTPError as exc:
                        exc.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
                        last_code = f"ai.http_{exc.code}"
                        last_retryable = exc.code in RETRYABLE_HTTP_STATUSES
                    except (urllib.error.URLError, TimeoutError, socket.timeout):
                        last_code = "ai.network_error"
                        last_retryable = True
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
                        last_code = "ai.invalid_response"
                        last_retryable = False
                    if not last_retryable or attempt > self.config.max_retries:
                        break
                    self._check_interrupted(check_interrupted)
                    self.sleeper(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))
                    self._check_interrupted(check_interrupted)
        except _AiProviderInterruptSignal as exc:
            metrics = self._metrics(
                caller=caller,
                data_type=data_type,
                status="interrupted",
                attempts=attempts,
                started=started,
                error_code="ai.interrupted",
            )
            self._log(metrics)
            raise AiProviderInterruptedError(metrics=metrics) from exc.__cause__

        metrics = self._metrics(
            caller=caller,
            data_type=data_type,
            status="failed",
            attempts=attempts,
            started=started,
            error_code=last_code,
        )
        self._log(metrics)
        raise AiProviderError(
            last_code,
            "视觉识别服务暂时不可用，请稍后重试。",
            metrics=metrics,
            retryable=last_retryable,
        )

    @staticmethod
    def _check_interrupted(check_interrupted: Callable[[], None] | None) -> None:
        if check_interrupted is None:
            return
        try:
            check_interrupted()
        except Exception as exc:
            raise _AiProviderInterruptSignal from exc

    def _completion(
        self,
        response_payload: dict[str, Any],
        *,
        caller: str,
        data_type: str,
        attempts: int,
        started: float,
    ) -> VisionCompletion:
        content = response_payload["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Provider response content is empty.")
        usage_value = response_payload.get("usage")
        raw_usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
        usage = {
            "prompt_tokens": int(raw_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(raw_usage.get("completion_tokens") or 0),
            "total_tokens": int(raw_usage.get("total_tokens") or 0),
        }
        metrics = self._metrics(
            caller=caller,
            data_type=data_type,
            status="succeeded",
            attempts=attempts,
            started=started,
            usage=usage,
        )
        self._log(metrics)
        return VisionCompletion(
            content=content,
            response_model=str(response_payload.get("model") or self.config.model),
            usage=usage,
            metrics=metrics,
        )

    def _metrics(
        self,
        *,
        caller: str,
        data_type: str,
        status: str,
        attempts: int,
        started: float,
        usage: dict[str, int] | None = None,
        error_code: str = "",
    ) -> AiCallMetrics:
        usage = usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        estimated_cost = (
            prompt_tokens * self.config.input_cost_per_million
            + completion_tokens * self.config.output_cost_per_million
        ) / 1_000_000
        return AiCallMetrics(
            provider=self.config.provider,
            model=self.config.model,
            data_type=data_type,
            caller=caller,
            status=status,
            attempts=attempts,
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(usage.get("total_tokens") or prompt_tokens + completion_tokens),
            estimated_cost_usd=round(estimated_cost, 8),
            error_code=error_code,
        )

    @staticmethod
    def _log(metrics: AiCallMetrics) -> None:
        logger.info("AI provider call", extra={"ai_call": metrics.audit_payload()})
