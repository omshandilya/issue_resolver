"""Patch and branch helpers built on GitPython."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from git import GitCommandError, Repo


def _result(success: bool, **data: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"success": success}
    payload.update(data)
    return payload


def _open_repo(repo_path: str) -> Repo:
    return Repo(Path(repo_path))


def apply_diff(repo_path: str, filepath: str, new_content: str) -> dict[str, Any]:
    """Write *new_content* to *filepath* inside *repo_path* and return the resulting git diff."""
    try:
        repo = _open_repo(repo_path)
        file_path = Path(repo_path) / filepath
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(new_content, encoding="utf-8")
        diff_text = repo.git.diff("--", filepath)
        return _result(True, filepath=str(file_path), diff=diff_text)
    except Exception as exc:
        return _result(False, error=str(exc))


def create_branch(repo_path: str, branch_name: str) -> dict[str, Any]:
    """Create and check out a new git branch in *repo_path*."""
    try:
        repo = _open_repo(repo_path)
        branch = repo.create_head(branch_name)
        branch.checkout()
        return _result(True, branch=branch.name, active_branch=repo.active_branch.name)
    except GitCommandError as exc:
        return _result(False, error=str(exc))
    except Exception as exc:
        return _result(False, error=str(exc))


def generate_diff(repo_path: str) -> dict[str, Any]:
    """Return a unified diff for the current changes.

    Strategy:
    1. ``git diff`` — unstaged working-tree changes (normal agent flow).
    2. ``git diff master`` — if the working tree is clean because the fix was
       already committed ahead of master (e.g. on a re-run).
    3. ``git diff HEAD~1`` — last resort when neither of the above yields output.
    """
    try:
        repo = _open_repo(repo_path)

        # 1. Unstaged changes
        diff_text = repo.git.diff()
        if diff_text.strip():
            return _result(True, diff=diff_text)

        # 2. Committed changes ahead of master
        try:
            diff_text = repo.git.diff("master")
            if diff_text.strip():
                return _result(True, diff=diff_text)
        except GitCommandError:
            pass

        # 3. Last resort: diff against previous commit
        try:
            diff_text = repo.git.diff("HEAD~1")
            if diff_text.strip():
                return _result(True, diff=diff_text)
        except GitCommandError:
            pass

        return _result(True, diff="")
    except Exception as exc:
        return _result(False, error=str(exc))