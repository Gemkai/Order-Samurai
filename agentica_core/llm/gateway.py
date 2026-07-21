import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from agentica_core.llm.local_guards import extract_message_text, floor_max_tokens

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

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from langfuse import Langfuse

    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False

try:
    from safety.PII_scrubber import scrub_text
except ImportError:
    def scrub_text(text):
        return text

try:
    from execution.guardrails import AIGuardrails, GuardrailException
except ImportError:
    class GuardrailException(Exception):
        pass

    class AIGuardrails:
        @staticmethod
        def validate_input(user_prompt: str, max_length: int = 100000) -> str:
            if len(user_prompt) > max_length:
                raise GuardrailException(
                    f"Prompt exceeds maximum length of {max_length} characters."
                )
            return user_prompt

        @staticmethod
        def validate_output_json(llm_response: str, required_keys: Optional[list] = None) -> dict:
            data = json.loads(llm_response)
            missing = [key for key in (required_keys or []) if key not in data]
            if missing:
                raise GuardrailException(f"Missing required keys: {missing}")
            return data

try:
    from execution.nuclear_option_hook import NuclearOption
except ImportError:
    class NuclearOption:
        @staticmethod
        def inspect(content: str, context: str = "general") -> Tuple[bool, str]:
            return False, ""

try:
    from execution.telemetry import log_execution
except ImportError:
    def log_execution(*args, **kwargs):
        return None


MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_PROMPT_LENGTH = 100000
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"
LOCAL_MODEL = os.getenv("LOCAL_MODEL_NAME", "gemma4:4b")
OLLAMA_TIMEOUT_SEC = float(os.getenv("OLLAMA_TIMEOUT_SEC", "5"))

FREE_CHAIN = [
    "google/gemini-2.0-flash-exp:free",
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-nemo:free",
    "qwen/qwen-2-72b-instruct:free",
]

PREMIUM_CHAIN = [
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


class InfrastructureError(Exception):
    pass


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

        global_env = ROOT_DIR / ".env"
        if global_env.exists():
            load_dotenv(global_env)

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
        **kwargs,
    ) -> Any:
        provider_kwargs = dict(kwargs)
        base_tags = list(provider_kwargs.pop("tags", []))

        if model_chain:
            chain = list(model_chain)
        elif model:
            chain = [model]
            if ":free" not in model.lower():
                chain.extend(FREE_CHAIN)
        else:
            chain = PREMIUM_CHAIN if self.default_tier == "PREMIUM" else FAST_CHAIN

        if self.local_enabled and LOCAL_MODEL not in chain:
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
                elif target_model == LOCAL_MODEL:
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
        tool_calls: Any = 0,
        tool_latencies: Optional[list] = None,
        mod_type: str = "READ",
    ) -> dict:
        safe_prompt = AIGuardrails.validate_input(prompt, max_length=MAX_PROMPT_LENGTH)

        is_blocked, block_msg = NuclearOption.inspect(safe_prompt, context=task_name)
        if is_blocked:
            raise InfrastructureError(block_msg)

        active_tier = (tier or self.default_tier or "PREMIUM").upper()
        model_chain = self._build_legacy_chain(requested_model, active_tier)
        system_instruction = DEFAULT_SYSTEM_INSTRUCTION
        if required_json_keys:
            system_instruction += " Return ONLY valid JSON."

        start_time = time.time()
        response = self.generate_text(
            prompt=safe_prompt,
            system_instruction=system_instruction,
            temperature=0.0,
            model_chain=model_chain,
            response_schema={"type": "object"} if required_json_keys else None,
            return_metadata=True,
            tags=[f"task:{task_name}", f"context:{project_context}"],
        )

        content = response["text"]
        parsed = self._parse_legacy_content(content, required_json_keys)
        latency_ms = (time.time() - start_time) * 1000
        tool_call_count, tool_call_names = self._normalize_tool_calls(
            tool_calls, tool_latencies or []
        )

        log_execution(
            task_name=task_name,
            model_tier=self._classify_model_tier(response["model"], active_tier),
            latency_ms=latency_ms,
            tokens_in=len(safe_prompt),
            tokens_out=len(content),
            status="success",
            project=project_context,
            tool_calls=tool_call_count,
            tool_calls_list=tool_call_names,
            tool_latencies=tool_latencies or [],
            mod_type=mod_type,
            session_id=os.environ.get("CONVERSATION_ID"),
        )
        return parsed

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
        try:
            return AIGuardrails.validate_output_json(
                json_candidate, required_keys=required_json_keys
            )
        except Exception:
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

    def _normalize_tool_calls(
        self, tool_calls: Any, tool_latencies: List[dict]
    ) -> Tuple[int, List[str]]:
        names: List[str] = []
        if isinstance(tool_calls, (list, tuple, set)):
            names = [str(item) for item in tool_calls]
            return len(names), names

        if isinstance(tool_calls, dict):
            names = [str(key) for key in tool_calls.keys()]
            return len(names), names

        if not names and tool_latencies:
            names = [
                str(tool.get("tool"))
                for tool in tool_latencies
                if isinstance(tool, dict) and tool.get("tool")
            ]

        try:
            count = int(tool_calls or 0)
        except (TypeError, ValueError):
            count = len(names)

        return count, names

    def _classify_model_tier(self, model: str, requested_tier: str) -> str:
        normalized = model.lower()
        if normalized == LOCAL_MODEL.lower():
            return "LOCAL"
        if ":free" in normalized or normalized.startswith("openrouter/"):
            return "FREE"
        if "flash" in normalized:
            return "FAST"
        if normalized.startswith("anthropic/") or "pro" in normalized:
            return "PREMIUM"
        return requested_tier

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
                            "maxOutputTokens": 4096,
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
                        params={"key": key},
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
                    return scrub_text(text)
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
                return scrub_text(text)
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
            "HTTP-Referer": "https://order-samurai.local",
            "X-Title": "Order Samurai",
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
                return scrub_text(text)
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
            "max_tokens": 4096,
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
                return scrub_text(text)
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
        return scrub_text(text)

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
                output=scrub_text(response_text),
                model=model,
                usage=usage or {},
                metadata=metadata or {},
                tags=tags or [],
            )
        except Exception:
            pass


gateway = LLMGateway()


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
