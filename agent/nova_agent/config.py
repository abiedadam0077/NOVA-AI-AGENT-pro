"""Configuration for the NOVA agent (env-driven, CLI-overridable)."""

import os
from dataclasses import dataclass

DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class AgentConfig:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    workspace: str = "."
    max_iterations: int = 25
    command_timeout: int = 300
    max_output_chars: int = 20000
    temperature: float = 0.2

    @classmethod
    def from_env(cls, workspace: str = ".") -> "AgentConfig":
        def _int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, "") or default)
            except ValueError:
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, "") or default)
            except ValueError:
                return default

        return cls(
            model=os.environ.get("NOVA_MODEL", "") or DEFAULT_MODEL,
            base_url=os.environ.get("NOVA_OPENROUTER_BASE_URL", "") or DEFAULT_BASE_URL,
            api_key=os.environ.get("OPENROUTER_API_KEY", "") or "",
            workspace=workspace,
            max_iterations=_int("NOVA_MAX_ITERATIONS", 25),
            command_timeout=_int("NOVA_COMMAND_TIMEOUT", 300),
            max_output_chars=_int("NOVA_MAX_OUTPUT_CHARS", 20000),
            temperature=_float("NOVA_TEMPERATURE", 0.2),
        )
