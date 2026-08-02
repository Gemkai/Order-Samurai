"""Multi-provider LLM gateway (Gemini / Anthropic / OpenAI / OpenRouter / local Ollama).

Privacy model (2026-07-26, audit W2): the enforced control for sensitive data is
fail-closed routing — `local_only=True` (or a pinned single-model `model_chain`
plus the caller-side silent-fallback guard on `return_metadata`), which keeps the
prompt on this machine. This file previously carried an Antigravity-heritage
safety stack (PII scrubber, AIGuardrails, NuclearOption, telemetry) behind
`except ImportError` stubs; with ROOT_DIR pointing inside agentica_core/ every
import silently resolved to a no-op on every call. Those stubs were removed
rather than wired: their real implementations live with the Antigravity gateway
copy (sub-bundles/antigravity/llm/gateway.py, a separate update surface), and
every Governance consumer routes local-pinned, where output scrubbing adds
nothing. Do not reintroduce silent-fallback safety imports here.
"""
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from agentica_core.llm.local_guards import (
    LOCAL_TIMEOUT_SEC,
    extract_message_text,
    floor_max_tokens,
)

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path: Optional[str] = None, *args, **kwargs):
        env_path = Path(dotenv_path) if dotenv_path else Path(".env")
        if not env_path.exists():
            return False
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()
        return True

try:
    from langfuse import Langfuse

    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False


MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_PROMPT_LENGTH = 100000
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"
LOCAL_MODEL = os.getenv("LOCAL_MODEL_NAME", "gemma4:4b")
# Single home for the local-timeout rule: local_guards.LOCAL_TIMEOUT_SEC.
OLLAMA_TIMEOUT_SEC = LOCAL_TIMEOUT_SEC  # re-export for existing importers/tests
CLOUD_TIMEOUT_SEC = 60

# Canonical task-tier roster used by the lightweight scout/mechanism facade in
# agentica_core.model_router. Provider execution lives in this module only; the
# facade preserves the stable public import without maintaining a second HTTP
# stack, retry policy, or model roster.
ROUTED_MODELS: Dict[str, Dict[str, str]] = {
    "claude": {
        "classification": "claude-haiku-4-5-20251001",
        "analysis": "claude-sonnet-4-6",
    },
    "gemini": {
        "classification": "gemini-2.5-flash",
        "analysis": "gemini-2.5-flash",
    },
    "ollama": {
        "classification": "gemma4:4b",
        "analysis": "qwen3.6:35b",
    },
    "openrouter": {
        "classification": "meta-llama/llama-3.3-70b-instruct:free",
        "analysis": "meta-llama/llama-3.3-70b-instruct:free",
    },
}

FREE_CHAIN = [
    "google/gemini-2.0-flash-exp:free",
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-nemo:free",
    "qwen/qwen-2-72b-instruct:free",
]

# Quality-first: strongest Claude Sonnet first (direct Anthropic), matching
# model_router's Claude-first order. The stale claude-3.5-sonnet entry stays as
# a keyless-OpenRouter Claude fallback.
PREMIUM_CHAIN = [
    "anthropic/claude-sonnet-4-6",
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "anthropic/claude-3.5-sonnet",
    "gemini-3-flash-preview",
    LOCAL_MODEL,
] + FREE_CHAIN

FAST_CHAIN = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    LOCAL_MODEL,
] + FREE_CHAIN

LOCAL_FIRST_CHAIN = [
    LOCAL_MODEL,
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
    "openai/gpt-4o-mini",
] + FREE_CHAIN

LEGACY_MODEL_ALIASES = {
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
    "gemini-3-pro-preview": "gemini-3-pro-preview",
    "gemini-3-flash-preview": "gemini-3-flash-preview",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "gemini-2.0-flash": "gemini-2.5-flash",
    "gemini-2.0-flash-lite": "gemini-2.5-flash-lite",
    "gemini-2.0-pro": "gemini-2.5-pro",
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-flash-latest": "gemini-2.5-flash",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-flash": "gemini-2.5-flash",
    "gemini-pro": "gemini-2.5-pro",
    "google/gemini-2.0-flash-exp:free": "google/gemma-3-12b-it:free",
    "gemini-2.0-flash-exp:free": "google/gemma-3-12b-it:free",
}

ANTHROPIC_MODEL_ALIASES = {
    "claude-3-opus": "claude-3-opus-20240229",
    "claude-3-sonnet": "claude-3-5-sonnet-latest",
    "claude-3-haiku": "claude-3-5-haiku-latest",
    "claude-3.5-sonnet": "claude-3-5-sonnet-latest",
    # current Sonnet (matches model_router's direct-Anthropic id)
    "claude-sonnet-4-6": "claude-sonnet-4-6",
}

OPENROUTER_MODEL_ALIASES = {
    "qwen/qwen-2-72b-instruct:free": "qwen/qwen-2-72b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free": "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-2-9b-it:free": "google/gemma-2-9b-it:free",
    "mistralai/mistral-nemo:free": "mistralai/mistral-nemo:free",
}

OPENROUTER_ANTHROPIC_MODEL_ALIASES = {
    "claude-3-sonnet": "claude-3.5-sonnet",
    "claude-3-5-sonnet-latest": "claude-3.5-sonnet",
    "claude-3.5-sonnet": "claude-3.5-sonnet",
    "claude-3-haiku": "claude-3.5-haiku",
    "claude-3-5-haiku-latest": "claude-3.5-haiku",
    "claude-3.5-haiku": "claude-3.5-haiku",
}

DEFAULT_SYSTEM_INSTRUCTION = "You are the Antigravity Synthesis Engine."


def _safe_console_text(value: Any) -> str:
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


def _dedupe_chain(models: List[str]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for model in models:
        if not model:
            continue
        key = model.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(model)
    return ordered


class LLMGateway:
    def __init__(self, env_path: Optional[str] = None):
        if env_path and os.path.exists(env_path):
            load_dotenv(env_path)

        load_dotenv()

        self.gemini_primary_key = os.getenv("GEMINI_API_KEY")
        self.gemini_paid_key = os.getenv("GEMINI_PAID_API_KEY", "").strip() or None
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip() or None
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip() or None

        self.gemini_keys = [
            key for key in [self.gemini_primary_key, self.gemini_paid_key] if key
        ]

        if _LANGFUSE_AVAILABLE and os.getenv("LANGFUSE_PUBLIC_KEY"):
            try:
                self.langfuse = Langfuse()
            except Exception as exc:
                print(
                    f"[Gateway] Langfuse initialization failed (silent): {_safe_console_text(exc)}",
                    file=sys.stderr,
                )
                self.langfuse = None
        else:
            self.langfuse = None

        self.default_tier = os.getenv("LLM_DEFAULT_TIER", "PREMIUM").upper()
        self.local_enabled = (
            os.getenv("LOCAL_SAFETY_NET_ENABLED", "true").lower() == "true"
        )

    def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
        model_chain: Optional[List[str]] = None,
        return_metadata: bool = False,
        local_only: bool = False,
        **kwargs,
    ) -> Any:
        provider_kwargs = dict(kwargs)
        base_tags = list(provider_kwargs.pop("tags", []))

        if local_only:
            # Fail-closed privacy routing: keep only bare Ollama tags (name:tag,
            # no provider prefix) from the requested chain; never add cloud
            # models. A local failure raises instead of failing over to cloud.
            requested = list(model_chain or ([model] if model else []))
            chain = [
                m for m in requested
                if "/" not in m and ":" in m and ":free" not in m.lower()
            ] or [LOCAL_MODEL]
        elif model_chain:
            chain = list(model_chain)
        elif model:
            chain = [model]
            if ":free" not in model.lower():
                chain.extend(FREE_CHAIN)
        else:
            chain = PREMIUM_CHAIN if self.default_tier == "PREMIUM" else FAST_CHAIN

        if not local_only and self.local_enabled and LOCAL_MODEL not in chain:
            chain = list(chain) + [LOCAL_MODEL]

        if len(prompt) > MAX_PROMPT_LENGTH:
            raise ValueError(
                f"Prompt exceeds limit of {MAX_PROMPT_LENGTH} characters."
            )

        last_error = None
        for index, target_model in enumerate(_dedupe_chain(chain)):
            try:
                current_tags = list(base_tags)
                if index > 0:
                    current_tags.append(f"fallback-level-{index}")

                if ":free" in target_model.lower():
                    response_text = self._call_openrouter(
                        model=target_model,
                        prompt=prompt,
                        system_instruction=system_instruction,
                        temperature=temperature,
                        tags=current_tags,
                        fallback_index=index,
                        **provider_kwargs,
                    )
                elif "/" in target_model:
                    provider, model_name = target_model.split("/", 1)
                    if provider == "anthropic":
                        if self.anthropic_key:
                            response_text = self._call_anthropic(
                                model=model_name,
                                prompt=prompt,
                                system_instruction=system_instruction,
                                temperature=temperature,
                                tags=current_tags,
                                fallback_index=index,
                                **provider_kwargs,
                            )
                        else:
                            response_text = self._call_openrouter(
                                model=target_model,
                                prompt=prompt,
                                system_instruction=system_instruction,
                                temperature=temperature,
                                tags=current_tags,
                                fallback_index=index,
                                **provider_kwargs,
                            )
                    elif provider == "google":
                        if model_name.lower().startswith("gemini-"):
                            response_text = self._call_gemini(
                                model=model_name,
                                prompt=prompt,
                                system_instruction=system_instruction,
                                temperature=temperature,
                                tags=current_tags,
                                fallback_index=index,
                                **provider_kwargs,
                            )
                        else:
                            response_text = self._call_openrouter(
                                model=target_model,
                                prompt=prompt,
                                system_instruction=system_instruction,
                                temperature=temperature,
                                tags=current_tags,
                                fallback_index=index,
                                **provider_kwargs,
                            )
                    elif provider == "openai":
                        response_text = self._call_openai(
                            model=model_name,
                            prompt=prompt,
                            system_instruction=system_instruction,
                            temperature=temperature,
                            tags=current_tags,
                            fallback_index=index,
                            **provider_kwargs,
                        )
                    else:
                        response_text = self._call_openrouter(
                            model=target_model,
                            prompt=prompt,
                            system_instruction=system_instruction,
                            temperature=temperature,
                            tags=current_tags,
                            fallback_index=index,
                            **provider_kwargs,
                        )
                elif target_model == LOCAL_MODEL or (
                    # bare Ollama tag (name:tag, no provider prefix) — any installed
                    # local model, not just the env-pinned LOCAL_MODEL. Without this,
                    # gemma4:12b / qwen3.6:35b misrouted to _call_gemini and failed.
                    "/" not in target_model and ":" in target_model
                ):
                    response_text = self._call_local(
                        model=target_model,
                        prompt=prompt,
                        system_instruction=system_instruction,
                        temperature=temperature,
                        tags=current_tags,
                        fallback_index=index,
                        **provider_kwargs,
                    )
                else:
                    response_text = self._call_gemini(
                        model=target_model,
                        prompt=prompt,
                        system_instruction=system_instruction,
                        temperature=temperature,
                        tags=current_tags,
                        fallback_index=index,
                        **provider_kwargs,
                    )

                if return_metadata:
                    return {
                        "text": response_text,
                        "model": target_model,
                        "fallback_index": index,
                    }
                return response_text
            except Exception as exc:
                last_error = exc
                err_msg = str(exc).lower()
                safe_err_msg = _safe_console_text(err_msg)
                is_credit_error = any(
                    token in err_msg
                    for token in [
                        "402",
                        "403",
                        "insufficient credits",
                        "quota exhausted",
                        "balance",
                    ]
                )
                if is_credit_error:
                    print(
                        f"[Gateway] credit failover from {target_model}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[Gateway] model {target_model} failed: {safe_err_msg[:120]}",
                        file=sys.stderr,
                    )
                if index == len(chain) - 1:
                    raise last_error

        raise last_error or RuntimeError("No model chain available.")

    def call_llm(
        self,
        task_name: str,
        prompt: str,
        requested_model: str = "anthropic/claude-3.5-sonnet",
        tier: Optional[str] = None,
        required_json_keys: Optional[list] = None,
        project_context: str = "HUB",
    ) -> dict:
        # Prompt-length enforcement lives in generate_text (MAX_PROMPT_LENGTH).
        active_tier = (tier or self.default_tier or "PREMIUM").upper()
        model_chain = self._build_legacy_chain(requested_model, active_tier)
        system_instruction = DEFAULT_SYSTEM_INSTRUCTION
        if required_json_keys:
            system_instruction += " Return ONLY valid JSON."

        response = self.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=0.0,
            model_chain=model_chain,
            response_schema={"type": "object"} if required_json_keys else None,
            return_metadata=True,
            tags=[f"task:{task_name}", f"context:{project_context}"],
        )
        return self._parse_legacy_content(response["text"], required_json_keys)

    def _build_legacy_chain(
        self, requested_model: Optional[str], active_tier: str
    ) -> List[str]:
        normalized_model = self._normalize_requested_model(requested_model, active_tier)
        if active_tier == "LOCAL_FIRST":
            chain = list(LOCAL_FIRST_CHAIN)
            if normalized_model and normalized_model not in chain:
                chain.append(normalized_model)
            return _dedupe_chain(chain)

        base_chain = {
            "PREMIUM": PREMIUM_CHAIN,
            "FAST": FAST_CHAIN,
            "FREE": ["openrouter/auto"] + FREE_CHAIN,
            "LOCAL": [LOCAL_MODEL],
        }.get(active_tier, PREMIUM_CHAIN)

        chain: List[str] = []
        if active_tier == "LOCAL":
            return [LOCAL_MODEL]

        if normalized_model:
            if active_tier == "FREE" and ":free" not in normalized_model.lower():
                chain.append("openrouter/auto")
            else:
                chain.append(normalized_model)
        elif active_tier == "FREE":
            chain.append("openrouter/auto")

        chain.extend(base_chain)
        return _dedupe_chain(chain)

    def _normalize_requested_model(
        self, requested_model: Optional[str], active_tier: str
    ) -> Optional[str]:
        requested = (requested_model or "").strip()
        if not requested:
            return None

        lowered = requested.lower()
        if lowered in LEGACY_MODEL_ALIASES:
            return LEGACY_MODEL_ALIASES[lowered]

        if lowered.startswith("openrouter/"):
            routed_model = self._normalize_openrouter_model(requested)
            return f"openrouter/{routed_model}" if routed_model else requested

        if lowered.startswith("google/"):
            _, model_name = requested.split("/", 1)
            model_key = model_name.lower()
            if model_key.startswith("gemini-"):
                return LEGACY_MODEL_ALIASES.get(model_key, model_name)
            return self._normalize_openrouter_model(requested)

        if lowered.startswith("anthropic/"):
            _, model_name = requested.split("/", 1)
            actual_name = ANTHROPIC_MODEL_ALIASES.get(model_name.lower(), model_name)
            return f"anthropic/{actual_name}"

        if lowered.startswith("openai/"):
            return requested

        if "/" in requested:
            return self._normalize_openrouter_model(requested)

        if active_tier == "FAST" and "pro" in lowered:
            return "gemini-2.5-flash"

        return LEGACY_MODEL_ALIASES.get(lowered, requested)

    def _normalize_openrouter_model(self, model: str) -> str:
        routed_model = (model or "").strip()
        if not routed_model:
            return routed_model

        if routed_model.lower().startswith("openrouter/"):
            routed_model = routed_model.split("/", 1)[1].strip()

        lowered = routed_model.lower()
        if lowered in OPENROUTER_MODEL_ALIASES:
            return OPENROUTER_MODEL_ALIASES[lowered]

        if lowered.startswith("anthropic/"):
            _, model_name = routed_model.split("/", 1)
            normalized = OPENROUTER_ANTHROPIC_MODEL_ALIASES.get(
                model_name.lower(), model_name
            )
            return f"anthropic/{normalized}"

        return routed_model

    def _parse_legacy_content(
        self, content: str, required_json_keys: Optional[list]
    ) -> dict:
        if not required_json_keys:
            return {"content": content}

        json_candidate = self._extract_json_object(content)
        parsed = json.loads(json_candidate)
        missing = [key for key in required_json_keys if key not in parsed]
        if missing:
            raise ValueError(f"Missing required JSON keys: {missing}")
        return parsed

    def parse_jsonish_payload(self, result: Any) -> Dict[str, Any]:
        if isinstance(result, dict):
            if "content" in result and isinstance(result["content"], str):
                content = result["content"].strip()
            else:
                return result
        else:
            content = str(result).strip()

        candidate = self._extract_json_object(content)
        try:
            payload = json.loads(candidate)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _extract_json_object(self, content: str) -> str:
        cleaned = content.strip()
        cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        return match.group(1) if match else cleaned

    def _call_gemini(self, model: str, prompt: str, **kwargs) -> str:
        if not self.gemini_keys:
            raise ValueError("No Gemini API keys configured.")

        actual_model = LEGACY_MODEL_ALIASES.get(model.lower().strip(), model)
        system_instruction = kwargs.get("system_instruction")
        temperature = kwargs.get("temperature", 0.0)
        fallback_index = kwargs.get("fallback_index", 0)
        tags = kwargs.get("tags", [])
        response_schema = kwargs.get("response_schema")

        last_exception = None
        for key_index, key in enumerate(self.gemini_keys):
            for attempt in range(MAX_RETRIES):
                try:
                    request_payload = {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [{"text": prompt}],
                            }
                        ],
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": int(kwargs.get("max_tokens") or 4096),
                        },
                    }
                    if system_instruction:
                        request_payload["systemInstruction"] = {
                            "parts": [{"text": system_instruction}]
                        }
                    if response_schema:
                        request_payload["generationConfig"]["responseMimeType"] = "application/json"

                    response = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{actual_model}:generateContent",
                        headers={"x-goog-api-key": key},
                        json=request_payload,
                        timeout=60,
                    )
                    response.raise_for_status()
                    response_json = response.json()
                    candidates = response_json.get("candidates") or []
                    if not candidates:
                        raise RuntimeError("Gemini returned no candidates.")

                    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
                    text = "".join(
                        part.get("text", "")
                        for part in parts
                        if isinstance(part, dict)
                    )
                    if not text:
                        raise RuntimeError("Gemini returned an empty text payload.")

                    usage = response_json.get("usageMetadata") or {}
                    self._log_langfuse_generation(
                        name="gemini-call",
                        prompt=prompt,
                        response_text=text,
                        model=actual_model,
                        usage={
                            "input": usage.get("promptTokenCount", 0),
                            "output": usage.get("candidatesTokenCount", 0),
                            "total": usage.get("totalTokenCount", 0),
                        },
                        metadata={
                            "provider": "google",
                            "fallback_index": fallback_index,
                            "key_index": key_index,
                            "system_instruction": system_instruction,
                        },
                        tags=tags,
                    )
                    return text
                except Exception as exc:
                    last_exception = exc
                    err_msg = str(exc).lower()
                    if "404" in err_msg or "not found" in err_msg:
                        break
                    if any(token in err_msg for token in ["429", "500", "overloaded", "quota"]):
                        if attempt == MAX_RETRIES - 1:
                            break
                        time.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5))
                    else:
                        break

        if last_exception:
            raise last_exception
        raise RuntimeError("Gemini call failed without an exception payload.")

    def _call_openai(self, model: str, prompt: str, **kwargs) -> str:
        if not self.openai_key:
            raise ValueError("OPENAI_API_KEY not configured.")

        system_instruction = kwargs.get("system_instruction")
        temperature = kwargs.get("temperature", 0.0)
        fallback_index = kwargs.get("fallback_index", 0)
        tags = kwargs.get("tags", [])

        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": int(kwargs.get("max_tokens") or 4096),
        }
        if kwargs.get("response_schema"):
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                response.raise_for_status()
                response_json = response.json()
                text = response_json["choices"][0]["message"]["content"]
                if not text:
                    raise RuntimeError("OpenAI returned an empty/null content payload.")
                self._log_langfuse_generation(
                    name="openai-direct-call",
                    prompt=prompt,
                    response_text=text,
                    model=model,
                    usage={
                        "input": response_json.get("usage", {}).get("prompt_tokens", 0),
                        "output": response_json.get("usage", {}).get("completion_tokens", 0),
                        "total": response_json.get("usage", {}).get("total_tokens", 0),
                    },
                    metadata={
                        "provider": "openai-direct",
                        "fallback_index": fallback_index,
                        "system_instruction": system_instruction,
                    },
                    tags=tags,
                )
                return text
            except Exception as exc:
                err_msg = str(exc).lower()
                if any(
                    token in err_msg
                    for token in ["429", "500", "502", "503", "504", "timeout"]
                ):
                    if attempt == MAX_RETRIES - 1:
                        raise exc
                    time.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5))
                else:
                    raise exc

        raise RuntimeError("OpenAI call failed without an exception payload.")

    def _call_openrouter(self, prompt: str, **kwargs) -> str:
        model = self._normalize_openrouter_model(kwargs.get("model", "openrouter/auto"))
        system_instruction = kwargs.get("system_instruction")
        temperature = kwargs.get("temperature", 0.0)
        fallback_index = kwargs.get("fallback_index", 0)
        tags = kwargs.get("tags", [])

        headers = {
            "HTTP-Referer": "https://antigravity-ai.com",
            "X-Title": "Antigravity JARVIS",
        }
        if self.openrouter_key:
            headers["Authorization"] = f"Bearer {self.openrouter_key}"

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    url="https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    data=json.dumps(
                        {
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "top_p": 1,
                            "max_tokens": int(kwargs.get("max_tokens") or 4096),
                        }
                    ),
                    timeout=60,
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"OpenRouter error {response.status_code}: {response.text}"
                    )

                payload = response.json()
                text = payload["choices"][0]["message"]["content"]
                if not text:
                    raise RuntimeError("OpenRouter returned an empty/null content payload.")
                self._log_langfuse_generation(
                    name="openrouter-call",
                    prompt=prompt,
                    response_text=text,
                    model=model,
                    usage={
                        "input": payload.get("usage", {}).get("prompt_tokens", 0),
                        "output": payload.get("usage", {}).get("completion_tokens", 0),
                        "total": payload.get("usage", {}).get("total_tokens", 0),
                    },
                    metadata={
                        "provider": "openrouter",
                        "fallback_index": fallback_index,
                        "system_instruction": system_instruction,
                    },
                    tags=tags,
                )
                return text
            except Exception as exc:
                err_msg = str(exc).lower()
                if any(
                    token in err_msg
                    for token in ["429", "500", "502", "503", "504", "timeout"]
                ):
                    if attempt == MAX_RETRIES - 1:
                        raise exc
                    time.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5))
                else:
                    raise exc

        raise RuntimeError("OpenRouter call failed without an exception payload.")

    def _call_anthropic(self, model: str, prompt: str, **kwargs) -> str:
        if not self.anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY not configured.")

        actual_model = ANTHROPIC_MODEL_ALIASES.get(model, model)
        system_instruction = kwargs.get("system_instruction")
        temperature = kwargs.get("temperature", 0.0)
        fallback_index = kwargs.get("fallback_index", 0)
        tags = kwargs.get("tags", [])

        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": actual_model,
            "max_tokens": int(kwargs.get("max_tokens") or 4096),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if system_instruction:
            payload["system"] = system_instruction

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                response.raise_for_status()
                response_json = response.json()
                text = response_json["content"][0]["text"]
                if not text:
                    raise RuntimeError("Anthropic returned an empty content payload.")
                self._log_langfuse_generation(
                    name="anthropic-direct-call",
                    prompt=prompt,
                    response_text=text,
                    model=actual_model,
                    usage={
                        "input": response_json.get("usage", {}).get("input_tokens", 0),
                        "output": response_json.get("usage", {}).get("output_tokens", 0),
                        "total": response_json.get("usage", {}).get("input_tokens", 0)
                        + response_json.get("usage", {}).get("output_tokens", 0),
                    },
                    metadata={
                        "provider": "anthropic-direct",
                        "fallback_index": fallback_index,
                        "system_instruction": system_instruction,
                    },
                    tags=tags,
                )
                return text
            except Exception as exc:
                err_msg = str(exc).lower()
                if any(
                    token in err_msg
                    for token in ["429", "500", "502", "503", "504", "timeout"]
                ):
                    if attempt == MAX_RETRIES - 1:
                        raise exc
                    time.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5))
                else:
                    raise exc

        raise RuntimeError("Anthropic call failed without an exception payload.")

    def _call_local(self, prompt: str, **kwargs) -> str:
        system_instruction = kwargs.get("system_instruction")
        temperature = kwargs.get("temperature", 0.0)
        target_model = kwargs.get("model", LOCAL_MODEL)
        fallback_index = kwargs.get("fallback_index", 0)
        tags = kwargs.get("tags", [])

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": target_model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": floor_max_tokens(kwargs.get("max_tokens")),
            },
            "stream": False,
        }
        if kwargs.get("num_ctx"):
            # always pin num_ctx for long prompts — Ollama's default window
            # silently truncates from the front, which reads as model stupidity
            payload["options"]["num_ctx"] = int(kwargs["num_ctx"])
        if "think" in kwargs:
            # thinking builds (qwen3.6, gemma4:12b on this Ollama) burn the whole
            # num_predict budget in `thinking` and return empty content unless
            # thinking is disabled — the exact failure that killed the local tier
            payload["think"] = bool(kwargs["think"])
        if kwargs.get("response_schema"):
            payload["format"] = "json"

        response = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT_SEC)
        response.raise_for_status()
        text = extract_message_text(response.json().get("message", {}))
        # extract_message_text's contract: callers MUST treat "" as a failure,
        # never a valid empty answer. Local is the last fallback link, so a
        # silent "" here would return an empty answer to the caller with no
        # fallback firing — the exact "silently dead" mode local_guards exists
        # to prevent. Raise so generate_text's except/fallback path handles it.
        if not text:
            raise RuntimeError(f"empty response from local model {target_model}")
        self._log_langfuse_generation(
            name="local-safety-net-call",
            prompt=prompt,
            response_text=text,
            model=target_model,
            usage=None,
            metadata={
                "provider": "local-ollama",
                "fallback_index": fallback_index,
                "system_instruction": system_instruction,
            },
            tags=tags or ["local-fallback"],
        )
        return text

    def _log_langfuse_generation(
        self,
        name: str,
        prompt: str,
        response_text: str,
        model: str,
        usage: Optional[dict],
        metadata: Optional[dict],
        tags: Optional[List[str]],
    ) -> None:
        if not self.langfuse:
            return

        try:
            self.langfuse.generation(
                name=name,
                input=prompt,
                output=response_text,
                model=model,
                usage=usage or {},
                metadata=metadata or {},
                tags=tags or [],
            )
        except Exception:
            pass


gateway = LLMGateway()


def call_routed_llm(
    system: str,
    user: str,
    task: str = "classification",
    max_tokens: int = 2048,
    temperature: float = 0.0,
    local_only: bool = False,
    brain: bool = False,
) -> Optional[str]:
    """Stable text-router contract for scouts and stateless model callers.

    This replaces the second provider implementation formerly housed in
    ``agentica_core.model_router``. That module is now only a compatibility
    facade; all HTTP, timeout, retry, empty-output, and model-selection behavior
    is owned here.

    ``local_only`` is fail closed: the chain contains exactly one local model,
    and any failure returns ``None``. Cloud mode retains the quality-first order
    Claude -> Gemini -> local Ollama -> OpenRouter, skipping providers that have
    no configured credential rather than making doomed network calls.
    """
    if task not in ("classification", "analysis"):
        return None
    if brain:
        try:
            # Brain³ is differentiated/private context and is deliberately not
            # part of the public export's static import closure. Resolve it only
            # when requested so the public gateway remains self-contained.
            brain_module = __import__(
                "agentica_core.brain_context", fromlist=["load_brain_context"]
            )
            preamble = brain_module.load_brain_context()
        except Exception:
            preamble = ""
        if preamble:
            system = f"{preamble}\n\n---\n\n{system}"

    routed = LLMGateway()
    local_model = ROUTED_MODELS["ollama"][task]
    if local_only:
        chain = [local_model]
    else:
        chain = []
        if routed.anthropic_key:
            chain.append(f"anthropic/{ROUTED_MODELS['claude'][task]}")
        if routed.gemini_keys:
            chain.append(ROUTED_MODELS["gemini"][task])
        chain.append(local_model)
        if routed.openrouter_key:
            chain.append(ROUTED_MODELS["openrouter"][task])

    try:
        result = routed.generate_text(
            prompt=user,
            system_instruction=system,
            temperature=temperature,
            model_chain=chain,
            local_only=local_only,
            max_tokens=max_tokens,
        )
        return result if isinstance(result, str) and result.strip() else None
    except Exception:
        return None


def generate_text(
    prompt: str,
    system_instruction: Optional[str] = None,
    temperature: float = 0.0,
    model: Optional[str] = None,
) -> str:
    return gateway.generate_text(
        prompt=prompt,
        system_instruction=system_instruction,
        temperature=temperature,
        model=model,
    )


def call_llm(*args, **kwargs) -> dict:
    return gateway.call_llm(*args, **kwargs)
