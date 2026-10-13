"""The NOVA agent loop: prompt → tool calls → results → repeat → finish."""

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .protocol import TOOL_SCHEMAS, ToolCall, extract_tool_calls
from .tools import ToolExecutor

SYSTEM_PROMPT = """You are NOVA Agent, an autonomous software-engineering agent working inside a sandboxed workspace (a GitHub Actions runner during CI, or a local test directory).

You receive a task from the user. Accomplish it using your tools:
- Inspect the workspace first (list_files / read_file) before changing anything.
- Make changes with write_file / edit_file.
- VERIFY your work with real commands via run_command (tests, linters, builds, scripts). Never claim something works without running it.
- If verification fails, read the error output, fix the code, and verify again.
- Keep changes minimal and focused on the task; do not reformat unrelated code.
- If you are asked to build an app, run the real build command and report its true outcome.
- When the task is complete (or you are truly blocked), call `finish` with an honest summary: what changed, how it was verified (with real command output), and anything left to do.

Tool-call format: prefer native function calling. If native function calling is unavailable to you, reply with exactly one fenced JSON block instead:
```json
{"tool": "<tool_name>", "args": { ... }}
```
"""


@dataclass
class RunResult:
    status: str = "error"  # success | error | max_iterations
    summary: str = ""
    iterations: int = 0
    changed_files: List[str] = field(default_factory=list)
    tool_log: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    model: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clip(text: Any, limit: int) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head
    return text[:head] + f"\n...[clipped {len(text) - limit} chars]...\n" + text[-tail:]


class AgentRunner:
    def __init__(
        self,
        provider: Any,
        executor: ToolExecutor,
        config: Any,
        git: Optional[Any] = None,
    ):
        self.provider = provider
        self.executor = executor
        self.config = config
        self.git = git

    def run(self, command: str) -> RunResult:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": str(command)},
        ]
        tool_log: List[Dict[str, Any]] = []
        summary = ""
        status = "max_iterations"
        error: Optional[str] = None
        iterations = 0

        for iteration in range(1, int(self.config.max_iterations) + 1):
            iterations = iteration
            try:
                message = self.provider.chat(
                    messages,
                    tools=TOOL_SCHEMAS,
                    temperature=getattr(self.config, "temperature", 0.2),
                )
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
                break
            if not isinstance(message, dict):
                status = "error"
                error = f"provider returned a non-dict message: {type(message).__name__}"
                break
            message.setdefault("role", "assistant")
            if message.get("content") is None:
                message["content"] = ""
            messages.append(message)

            calls, text = extract_tool_calls(message)
            if not calls:
                summary = (text or "").strip() or "(model finished without a summary)"
                status = "success"
                break

            finished = False
            for call in calls:
                entry, result_text = self._execute(call, iteration)
                tool_log.append(entry)
                if call.is_text_fallback:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"[tool_result {call.name}] ok={entry['ok']}\n{result_text}",
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": result_text,
                        }
                    )
                if call.name == "finish":
                    summary = (
                        str(call.arguments.get("summary", "")).strip()
                        or "(finished without summary)"
                    )
                    status = "success"
                    finished = True
                    break
            if finished:
                break

        changed: List[str] = []
        if self.git is not None:
            try:
                changed = self.git.changed_files()
            except Exception:  # noqa: BLE001
                changed = []
        if not changed:
            changed = sorted(self.executor.written_files)

        return RunResult(
            status=status,
            summary=summary,
            iterations=iterations,
            changed_files=changed,
            tool_log=tool_log,
            error=error,
            model=getattr(self.config, "model", ""),
        )

    def _execute(self, call: ToolCall, iteration: int) -> Tuple[Dict[str, Any], str]:
        args_clip = _clip(json.dumps(call.arguments, ensure_ascii=False), 2000)
        if call.name == "finish":
            entry = {
                "iteration": iteration,
                "tool": "finish",
                "args": args_clip,
                "ok": True,
                "output": "Agent finished.",
            }
            return entry, "Agent finished."
        result = self.executor.execute(call.name, call.arguments)
        output = _clip(result.get("output", ""), 8000)
        entry = {
            "iteration": iteration,
            "tool": call.name,
            "args": args_clip,
            "ok": bool(result.get("ok")),
            "output": output,
        }
        return entry, output
