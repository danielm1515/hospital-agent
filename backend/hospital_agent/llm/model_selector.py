"""Model Selector (spec §1; LLM design §3): which provider and model the server uses.

The server uses OpenAI only when OPENAI_API_KEY is set (from .env, which git ignores).
Without it select_provider() returns None and the Agent Orchestrator does not start -
there is no silent fallback to FakeProvider, which exists only for tests and obs.golden.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from .provider import LLMProvider, OpenAIProvider, prompts_version

DEFAULT_MODEL = "gpt-5.6-luna"


def select_provider(env: Mapping[str, str] = os.environ) -> LLMProvider | None:
    key = env.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    return OpenAIProvider(key, env.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL)


def llm_version(provider: LLMProvider) -> str:
    """The LLM part of rule_version (§18.5): the model and the hash of the four prompts."""
    return f"llm-{provider.model}-{prompts_version()}"
