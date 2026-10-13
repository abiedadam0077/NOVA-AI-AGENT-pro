"""Tool implementations for the NOVA agent.

Safety model (defense in depth — the real boundary is the disposable CI sandbox):
- Every file path is resolved and jailed inside the workspace.
- Shell commands run with a scrubbed environment (API keys / tokens removed).
- Obviously destructive system commands are denied by regex rules.
- Command output and file reads are truncated to protect the context window.
"""

import os
import re
import subprocess
from typing import Any, Dict, Set

EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "dist",
    ".dart_tool",
    ".next",
    ".gradle",
    ".idea",
    ".pytest_cache",
}

SENSITIVE_ENV_EXACT = {"OPENROUTER_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"}
SENSITIVE_ENV_PREFIXES = (
    "NOVA_APP_",
    "INPUT_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GCLOUD_",
    "DEPLOY_",
)

DENY_RULES = [
    (
        re.compile(r"\brm\b[^|;&]*\s-\w*[rf]\w*[^|;&]*\s/(\*?\s|\*?$|\*?;|\*?&|\*?\|)"),
        "rm -rf / (filesystem wipe)",
    ),
    (
        re.compile(
            r"\brm\b[^|;&]*\s-\w*[rf]\w*[^|;&]*\s/(etc|usr|bin|sbin|boot|dev|home|lib|lib64|proc|root|sys|var|opt|srv)\b"
        ),
        "rm -rf on a system directory",
    ),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "disk format"),
    (re.compile(r"\bdd\b[^|;&]*\bof=/dev/"), "raw device write"),
    (re.compile(r":\(\)\s*\{[^}]*\|[^}]*&[^}]*\}\s*;"), "fork bomb"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"), "power state change"),
    (re.compile(r"\bchmod\s+(-R\s+)?0?777\s+/(\s|$)"), "chmod 777 on root"),
    (re.compile(r"\bgit\s+push\b[^|;&]*(--force\b|-f\b)"), "force push"),
    (re.compile(r"\bsudo\b"), "sudo is not allowed"),
    (re.compile(r">\s*/dev/(sd|nvme|disk)"), "raw disk write"),
]

MAX_READ_BYTES = 1_000_000
LIST_FILES_CAP = 1000


class ToolError(Exception):
    """Raised for user-facing tool errors (reported back to the model)."""


class ToolExecutor:
    def __init__(
        self,
        workspace: str,
        command_timeout: int = 300,
        max_output_chars: int = 20000,
    ):
        self.workspace = os.path.realpath(workspace)
        if not os.path.isdir(self.workspace):
            raise ToolError(f"workspace not found: {workspace}")
        self.command_timeout = max(1, int(command_timeout))
        self.max_output_chars = max(1000, int(max_output_chars))
        self.written_files: Set[str] = set()

    # ------------------------------------------------------------------ API

    def execute(self, name: str, args: Any) -> Dict[str, Any]:
        if not isinstance(args, dict):
            args = {}
        handlers = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "run_command": self.run_command,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"ok": False, "output": f"ERROR: unknown tool '{name}'"}
        try:
            return handler(args)
        except ToolError as exc:
            return {"ok": False, "output": f"ERROR: {exc}"}
        except Exception as exc:  # noqa: BLE001 - errors are fed back to the model
            return {"ok": False, "output": f"ERROR: {type(exc).__name__}: {exc}"}

    # -------------------------------------------------------------- helpers

    def _resolve(self, path: Any) -> str:
        if path is None or not str(path).strip():
            raise ToolError("empty path")
        candidate = os.path.realpath(os.path.join(self.workspace, str(path)))
        if candidate != self.workspace and not candidate.startswith(
            self.workspace + os.sep
        ):
            raise ToolError(f"path escapes the workspace and was blocked: {path}")
        return candidate

    def _rel(self, abspath: str) -> str:
        return os.path.relpath(abspath, self.workspace)

    def _truncate(self, text: str) -> str:
        limit = self.max_output_chars
        if len(text) <= limit:
            return text
        head = limit // 3
        tail = limit - head
        return (
            text[:head]
            + f"\n...[truncated {len(text) - limit} chars]...\n"
            + text[-tail:]
        )

    def _clean_env(self) -> Dict[str, str]:
        env = {}
        for key, value in os.environ.items():
            if key in SENSITIVE_ENV_EXACT:
                continue
            if any(key.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
                continue
            env[key] = value
        return env

    # ---------------------------------------------------------------- tools

    def list_files(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = self._resolve(args.get("path", "."))
        if not os.path.isdir(root):
            raise ToolError(f"not a directory: {args.get('path')}")
        entries = []
        capped = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = -1
                entries.append((self._rel(full), size))
                if len(entries) >= LIST_FILES_CAP:
                    capped = True
                    break
            if capped:
                break
        if not entries:
            return {"ok": True, "output": "(no files found)"}
        entries.sort()
        output = "\n".join(f"{path} ({size} bytes)" for path, size in entries)
        if capped:
            output += f"\n... (listing capped at {LIST_FILES_CAP} files)"
        return {"ok": True, "output": output}

    def read_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args.get("path"))
        if not os.path.isfile(path):
            raise ToolError(f"file not found: {args.get('path')}")
        size = os.path.getsize(path)
        if size > MAX_READ_BYTES:
            raise ToolError(
                f"file too large to read directly ({size} bytes); "
                "use run_command with head/sed/grep instead"
            )
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        try:
            offset = max(1, int(args.get("offset") or 1))
        except (TypeError, ValueError):
            offset = 1
        selected = lines[offset - 1 :]
        limit = args.get("limit")
        if limit:
            try:
                selected = selected[: max(0, int(limit))]
            except (TypeError, ValueError):
                pass
        if not selected:
            return {"ok": True, "output": "(empty range)"}
        numbered = "".join(
            f"{offset + i}: {line if line.endswith(chr(10)) else line + chr(10)}"
            for i, line in enumerate(selected)
        )
        return {"ok": True, "output": self._truncate(numbered)}

    def write_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args.get("path"))
        content = args.get("content")
        if content is None:
            raise ToolError("'content' is required")
        content = str(content)
        parent = os.path.dirname(path) or self.workspace
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        self.written_files.add(self._rel(path))
        return {
            "ok": True,
            "output": f"wrote {len(content)} chars to {self._rel(path)}",
        }

    def edit_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args.get("path"))
        old = args.get("old_text")
        new = args.get("new_text")
        if old is None or new is None:
            raise ToolError("'old_text' and 'new_text' are required")
        old, new = str(old), str(new)
        if old == new:
            raise ToolError("old_text and new_text are identical")
        if not os.path.isfile(path):
            raise ToolError(f"file not found: {args.get('path')}")
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        count = text.count(old)
        if count == 0:
            raise ToolError(
                "old_text not found in file (it must match exactly, including whitespace)"
            )
        text = text.replace(old, new, 1)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        self.written_files.add(self._rel(path))
        note = (
            f" (note: {count} occurrences existed; replaced the first)"
            if count > 1
            else ""
        )
        return {"ok": True, "output": f"edited {self._rel(path)}{note}"}

    def run_command(self, args: Dict[str, Any]) -> Dict[str, Any]:
        command = args.get("command")
        if not command or not str(command).strip():
            raise ToolError("'command' is required")
        command = str(command)
        for pattern, label in DENY_RULES:
            if pattern.search(command):
                return {
                    "ok": False,
                    "output": (
                        f"BLOCKED by safety rule ({label}). Rewrite the command: "
                        "use relative paths inside the workspace, no sudo."
                    ),
                }
        cwd = self._resolve(args.get("cwd") or ".")
        if not os.path.isdir(cwd):
            raise ToolError(f"cwd is not a directory: {args.get('cwd')}")
        try:
            timeout = int(args.get("timeout") or self.command_timeout)
        except (TypeError, ValueError):
            timeout = self.command_timeout
        timeout = max(1, min(timeout, self.command_timeout))
        try:
            proc = subprocess.run(
                ["/bin/bash", "-lc", command],
                cwd=cwd,
                env=self._clean_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return {
                "ok": False,
                "output": f"ERROR: command timed out after {timeout}s\n{self._truncate(partial or '')}",
            }
        output = proc.stdout or ""
        if proc.stderr:
            output += "\n[stderr]\n" + proc.stderr
        ok = proc.returncode == 0
        header = f"exit code {proc.returncode}" + ("" if ok else " (FAILED)")
        return {"ok": ok, "output": f"{header}\n{self._truncate(output)}".strip()}
