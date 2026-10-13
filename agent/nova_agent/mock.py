"""Scripted provider used for local testing without an OpenRouter key."""

import json
import uuid
from typing import Any, Dict, List, Optional


class MockProvider:
    """Replays a scripted list of assistant messages, one per chat() call."""

    def __init__(self, script: Optional[List[Dict[str, Any]]] = None):
        self.script: List[Dict[str, Any]] = list(script or [])
        self.seen_messages: List[List[Dict[str, Any]]] = []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        self.seen_messages.append([dict(m) for m in messages])
        if self.script:
            return self.script.pop(0)
        return {"role": "assistant", "content": "No further actions."}


def tool_call_message(
    name: str,
    arguments: Dict[str, Any],
    call_id: Optional[str] = None,
    content: str = "",
) -> Dict[str, Any]:
    """Build a native (OpenAI-style) assistant message with one tool call."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call_id or f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments),
                },
            }
        ],
    }


def text_tool_message(obj: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Build an assistant message carrying a fallback JSON tool call in text."""
    return {
        "role": "assistant",
        "content": f"{prefix}\n```json\n{json.dumps(obj)}\n```".strip(),
    }
