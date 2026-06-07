"""Go build and test helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _result(success: bool, **data):
    payload = {"success": success}
    payload.update(data)
    return payload


def _run_go_command(repo_path, args):
    try:
        completed = subprocess.run(
            ["go", *args],
            cwd=Path(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
        return _result(
            completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def run_tests(repo_path, pkg="./..."):
    return _run_go_command(repo_path, ["test", pkg])


def run_build(repo_path):
    return _run_go_command(repo_path, ["build", "./..."])


def run_golint(repo_path):
    try:
        completed = subprocess.run(
            ["golint", "./..."],
            cwd=Path(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
        return _result(
            completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    except Exception as exc:
        return _result(False, error=str(exc))