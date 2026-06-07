"""GitHub REST API helpers."""

from __future__ import annotations

import os

import requests


def _result(success: bool, **data):
    payload = {"success": success}
    payload.update(data)
    return payload


def _headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_issue(owner, repo, issue_number):
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
        response = requests.get(url, headers=_headers(), timeout=30)
        data = response.json() if response.content else {}
        if response.ok:
            return _result(True, issue=data, status_code=response.status_code)
        return _result(False, issue=data, status_code=response.status_code, error="GitHub API request failed")
    except Exception as exc:
        return _result(False, error=str(exc))


def list_issues(owner, repo):
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        response = requests.get(url, headers=_headers(), timeout=30)
        data = response.json() if response.content else []
        if response.ok:
            return _result(True, issues=data, status_code=response.status_code)
        return _result(False, issues=data if isinstance(data, list) else [], status_code=response.status_code, error="GitHub API request failed")
    except Exception as exc:
        return _result(False, error=str(exc))