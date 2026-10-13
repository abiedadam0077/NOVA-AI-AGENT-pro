"""Human-readable reporting: GitHub step summary + console output."""

import os

from .runner import RunResult

STATUS_EMOJI = {"success": "✅", "error": "❌", "max_iterations": "⚠️"}


def to_step_summary(result: RunResult) -> str:
    lines = [
        f"## {STATUS_EMOJI.get(result.status, '❔')} NOVA Agent — `{result.status}`"
    ]
    if result.model:
        lines.append(f"**Model:** `{result.model}`  ")
    lines.append(f"**Iterations:** {result.iterations}")
    if result.error:
        lines.append(f"\n**Error:** `{result.error}`")
    if result.summary:
        lines.append("\n### Agent summary\n" + result.summary)
    if result.changed_files:
        lines.append("\n### Changed files")
        lines.extend(f"- `{path}`" for path in result.changed_files[:100])
    if result.tool_log:
        lines.append("\n### Tool calls")
        lines.append("| # | Tool | OK | Args |")
        lines.append("|---|------|----|------|")
        for entry in result.tool_log[:100]:
            args = str(entry.get("args", ""))
            if len(args) > 80:
                args = args[:77] + "..."
            args = args.replace("|", "\\|").replace("\n", " ")
            mark = "✔" if entry.get("ok") else "✘"
            lines.append(
                f"| {entry.get('iteration')} | `{entry.get('tool')}` | {mark} | {args} |"
            )
    return "\n".join(lines) + "\n"


def append_step_summary(text: str) -> None:
    """Append to $GITHUB_STEP_SUMMARY when running inside GitHub Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
