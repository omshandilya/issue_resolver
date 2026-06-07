"""Repository inspection helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from config import MAX_TOKENS_PER_FILE


def _result(success: bool, **data):
    payload = {"success": success}
    payload.update(data)
    return payload


def _run_command(command, cwd):
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed
    except Exception as exc:  # pragma: no cover - defensive fallback
        return exc


def list_files(repo_path):
    try:
        repo_dir = Path(repo_path)
        rg_command = ["rg", "--files", str(repo_dir)]
        completed = _run_command(rg_command, repo_dir)
        if hasattr(completed, "returncode") and completed.returncode == 0:
            files = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            return _result(
                True,
                files=files[:2],
                total_files=len(files),
                truncated=len(files) > 2,
            )

        grep_command = ["grep", "-R", "-l", "", str(repo_dir)]
        completed = _run_command(grep_command, repo_dir)
        if hasattr(completed, "returncode") and completed.returncode == 0:
            files = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            return _result(
                True,
                files=files[:2],
                total_files=len(files),
                truncated=len(files) > 2,
            )

        files = []
        for root, _, filenames in os.walk(repo_dir):
            for filename in filenames:
                files.append(str(Path(root) / filename))
        return _result(False, files=files[:2], total_files=len(files), truncated=len(files) > 2, error="rg and grep commands were unavailable or failed")
    except Exception as exc:
        return _result(False, error=str(exc))


def read_file(repo_path, filepath):
    try:
        file_path = Path(repo_path) / filepath
        raw_content = file_path.read_text(encoding="utf-8")
        truncated = len(raw_content) > MAX_TOKENS_PER_FILE
        if truncated:
            content = raw_content[:MAX_TOKENS_PER_FILE] + "\n...truncated for context"
        else:
            content = raw_content
        return _result(
            True,
            content=content,
            filepath=str(file_path),
            truncated=truncated,
            total_chars=len(raw_content),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def search_code(repo_path, query):
    try:
        repo_dir = Path(repo_path)
        rg_command = ["rg", "-n", "--no-heading", query, str(repo_dir)]
        completed = _run_command(rg_command, repo_dir)
        if hasattr(completed, "returncode") and completed.returncode == 0:
            matches = [line for line in completed.stdout.splitlines() if line.strip()]
            return _result(
                True,
                matches=matches[:5],
                total_matches=len(matches),
                truncated=len(matches) > 5,
            )

        grep_command = ["grep", "-RIn", query, str(repo_dir)]
        completed = _run_command(grep_command, repo_dir)
        if hasattr(completed, "returncode") and completed.returncode == 0:
            matches = [line for line in completed.stdout.splitlines() if line.strip()]
            return _result(
                True,
                matches=matches[:5],
                total_matches=len(matches),
                truncated=len(matches) > 5,
            )

        return _result(False, matches=[], total_matches=0, truncated=False, error="rg and grep searches failed")
    except Exception as exc:
        return _result(False, error=str(exc))


def get_git_log(repo_path, filepath):
    try:
        repo_dir = Path(repo_path)
        command = ["git", "log", "--oneline", "--", filepath]
        completed = subprocess.run(
            command,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            commits = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            return _result(
                True,
                commits=commits[:5],
                total_commits=len(commits),
                truncated=len(commits) > 5,
            )
        return _result(False, commits=[], total_commits=0, truncated=False, error="git log failed")
    except Exception as exc:
        return _result(False, error=str(exc))