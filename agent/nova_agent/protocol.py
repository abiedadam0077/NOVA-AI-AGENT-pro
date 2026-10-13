"""Tool-call protocol: OpenAI-style native function calling, with a tolerant
JSON-in-text fallback for models that cannot emit native tool calls.

Fallback format the model may print (also documented in the system prompt):

    ```json
    {"tool": "write_file", "args": {"path": "a.txt", "content": "hi"}}
    ```
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the workspace (recursive; excludes .git, node_modules, build dirs).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional subdirectory to list, relative to the workspace root.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the workspace, with numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "offset": {"type": "integer", "description": "First line to return (1-based)."},
                    "limit": {"type": "integer", "description": "Maximum number of lines to return."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content (creates parent directories).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace the FIRST exact occurrence of old_text with new_text in a file. old_text must match exactly, including whitespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Exact text to find."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a bash command inside the workspace. Use it to run tests, linters, builds and git commands. Sensitive environment variables are removed and destructive system commands are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute."},
                    "cwd": {"type": "string", "description": "Optional subdirectory (relative) to run in."},
                    "timeout": {"type": "integer", "description": "Seconds before the command is killed."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the task. Call exactly once when the work is complete or you are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "What was done, which files changed, how it was verified, and anything left to do.",
                    }
                },
                "required": ["summary"],
            },
        },
    },
]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_text_fallback(self) -> bool:
        return self.id.startswith("text_")


def _extract_json_calls(text: str) -> List[ToolCall]:
    """Extract fallback tool calls from message text (fenced or bare JSON)."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if not candidates:
        start = text.find("{")
        if start == -1:
            return []
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates = [text[start : i + 1]]
                    break
    calls: List[ToolCall] = []
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or "tool" not in obj:
            continue
        args = obj.get("args")
        if not isinstance(args, dict):
            args = obj.get("arguments")
        if not isinstance(args, dict):
            args = {}
        calls.append(
            ToolCall(id=f"text_{len(calls)}", name=str(obj["tool"]), arguments=args)
        )
    return calls


def extract_tool_calls(message: Dict[str, Any]) -> Tuple[List[ToolCall], str]:
    """Return (tool_calls, text_content) from an assistant message.

    Native tool_calls win; the JSON-in-text fallback is only used when the
    message contains no native calls.
    """
    calls: List[ToolCall] = []
    for index, tc in enumerate(message.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "")
        raw = fn.get("arguments")
        if isinstance(raw, dict):
            args = raw
        else:
            raw = raw if isinstance(raw, str) else json.dumps(raw)
            try:
                parsed = json.loads(raw or "{}")
                args = parsed if isinstance(parsed, dict) else {"_raw": raw}
            except (json.JSONDecodeError, ValueError):
                args = {"_unparsed": raw}
        call_id = str(tc.get("id") or f"call_{index}")
        calls.append(ToolCall(id=call_id, name=name, arguments=args))
    text = message.get("content") or ""
    if not calls and isinstance(text, str) and text.strip():
        calls = _extract_json_calls(text)
    return calls, (text if isinstance(text, str) else "")
