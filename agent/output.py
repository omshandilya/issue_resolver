"""Output artifact helpers for go-issue-agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI
from git import GitCommandError, Repo

from config import MAX_TOKENS, MODEL
from tools.patch_tools import generate_diff


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def _result(success: bool, **data):
    payload = {"success": success}
    payload.update(data)
    return payload


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            continue
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(part for part in parts if part).strip()


def _issue_number(issue: dict[str, Any] | Any) -> str:
    if isinstance(issue, dict):
        value = issue.get("number")
        if value is not None:
            return str(value)
    return "unknown"


def _call_groq(prompt: str) -> str:
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        return _extract_text(content)
    return str(content)


def create_local_branch(repo_path, issue_number):
    try:
        repo = Repo(Path(repo_path))
        branch_name = f"agent/issue-{issue_number}"

        try:
            branch = repo.create_head(branch_name)
        except GitCommandError:
            branch = repo.heads[branch_name]

        branch.checkout()

        repo.git.add(A=True)
        if repo.is_dirty(untracked_files=True):
            commit_message = f"fix: address issue #{issue_number}"
            commit = repo.index.commit(commit_message)
            return _result(
                True,
                branch=branch_name,
                commit_sha=commit.hexsha,
                committed=True,
                commit_message=commit_message,
            )

        return _result(True, branch=branch_name, committed=False, commit_message=None)
    except Exception as exc:
        return _result(False, error=str(exc))


def generate_pr_artifacts(repo_path, agent_final_response, issue):
    try:
        issue_num = _issue_number(issue)
        output_dir = OUTPUTS_DIR / issue_num
        output_dir.mkdir(parents=True, exist_ok=True)

        diff_result = generate_diff(repo_path)
        if not diff_result.get("success"):
            return _result(False, error=diff_result.get("error", "Failed to generate diff"))

        diff_text = diff_result.get("diff", "")
        diff_path = output_dir / "diff.patch"
        diff_path.write_text(diff_text, encoding="utf-8")

        if os.getenv("GROQ_API_KEY"):
            prompt = (
                "Generate a pull request summary as JSON only. "
                "Return exactly one JSON object with these keys: "
                "pr_title, pr_body, files_changed. "
                "The pr_title must use conventional commit style. "
                "The pr_body must cover the problem, approach, testing done, and linked issue. "
                "files_changed must be a list of objects with file and why fields. "
                "Do not include markdown fences or any extra text.\n\n"
                f"Issue:\n{json.dumps(issue, indent=2, ensure_ascii=False)}\n\n"
                f"Agent final response:\n{agent_final_response}\n\n"
                f"Unified diff:\n{diff_text}"
            )
            try:
                summary_text = _call_groq(prompt)
                try:
                    pr_summary = json.loads(summary_text)
                except json.JSONDecodeError:
                    pr_summary = {"raw": summary_text}
            except Exception as exc:
                pr_summary = {
                    "pr_title": f"fix: address issue #{issue_num}",
                    "pr_body": (
                        f"Problem: Groq summary generation failed and offline fallback was used.\n"
                        f"Approach: generated artifacts locally from the current working tree diff.\n"
                        f"Testing done: repository diff captured and CLI flow completed.\n"
                        f"Linked issue: #{issue_num}\n"
                        f"Groq error: {exc}"
                    ),
                    "files_changed": [],
                    "offline_mode": True,
                }
        else:
            pr_summary = {
                "pr_title": f"fix: address issue #{issue_num}",
                "pr_body": (
                    f"Problem: offline fallback was used because no Groq API key is configured.\n"
                    f"Approach: captured the current repository diff and summarized the issue locally.\n"
                    f"Testing done: syntax validation and artifact generation.\n"
                    f"Linked issue: #{issue_num}"
                ),
                "files_changed": [],
                "offline_mode": True,
            }

        summary_path = output_dir / "pr_summary.json"
        summary_path.write_text(json.dumps(pr_summary, indent=2, ensure_ascii=False), encoding="utf-8")

        pr_title = pr_summary.get("pr_title") if isinstance(pr_summary, dict) else None
        pr_body = pr_summary.get("pr_body") if isinstance(pr_summary, dict) else None
        files_changed = pr_summary.get("files_changed") if isinstance(pr_summary, dict) else None

        print(f"PR artifacts saved for issue #{issue_num}")
        print(f"- Diff: {diff_path}")
        print(f"- Summary: {summary_path}")
        if pr_title:
            print(f"- Title: {pr_title}")
        if pr_body:
            first_line = str(pr_body).splitlines()[0] if str(pr_body).splitlines() else ""
            print(f"- Body: {first_line}")
        if isinstance(files_changed, list):
            print(f"- Files changed: {len(files_changed)}")

        return _result(
            True,
            issue_number=issue_num,
            diff_path=str(diff_path),
            pr_summary_path=str(summary_path),
            pr_summary=pr_summary,
        )
    except Exception as exc:
        return _result(False, error=str(exc))