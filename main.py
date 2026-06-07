"""CLI entrypoint for go-issue-agent."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from config import APPROVED_REPOS, PROJECT_ROOT
from agent.core import run_agent
from agent.output import create_local_branch, generate_pr_artifacts
from tools.github_tools import list_issues


def _repo_url(repo_slug: str) -> str:
    return f"https://github.com/{repo_slug}.git"



def _repo_path(repo_slug: str) -> Path:
    return PROJECT_ROOT / APPROVED_REPOS[repo_slug]


def _ensure_clone(repo_slug: str) -> Path:
    repo_path = _repo_path(repo_slug)
    if repo_path.exists():
        return repo_path

    repo_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["git", "clone", _repo_url(repo_slug), str(repo_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "git clone failed").strip())
    return repo_path


def _print_json_section(title: str, payload: dict | list | str | None) -> None:
    print(title)
    if payload is None:
        print("  <none>")
        return
    if isinstance(payload, str):
        print(payload)
        return
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _reset_repo_to_master(repo_path: Path) -> None:
    """Checkout master and discard any local modifications so every solve
    run starts from a clean baseline and ``git diff`` captures the fresh fix."""
    try:
        from git import Repo, GitCommandError
        repo = Repo(repo_path)
        # Discard any uncommitted changes first
        repo.git.checkout("--", ".")
        # Switch to master (or main)
        for branch in ("master", "main"):
            try:
                repo.git.checkout(branch)
                break
            except GitCommandError:
                continue
    except Exception as exc:
        print(f"[warn] Could not reset repo to master: {exc}", file=sys.stderr)


def _handle_solve(args: argparse.Namespace) -> int:
    repo_slug = args.repo
    if repo_slug not in APPROVED_REPOS:
        print(
            "Repository not approved. Allowed values: "
            + ", ".join(APPROVED_REPOS.keys()),
            file=sys.stderr,
        )
        return 1

    repo_path = _ensure_clone(repo_slug)

    # Always start from a clean master so git diff captures the agent's changes
    _reset_repo_to_master(repo_path)

    owner, repo = repo_slug.split("/", 1)

    agent_result = run_agent(args.issue, owner=owner, repo=repo, repo_path=str(repo_path))
    issue = agent_result.get("issue") if isinstance(agent_result, dict) else None
    if issue is None:
        issue = {"number": args.issue}

    # Generate diff + PR summary BEFORE committing (working tree still dirty)
    artifacts_result = generate_pr_artifacts(str(repo_path), agent_result.get("final_response", ""), issue)

    # Now commit and create the branch
    if artifacts_result.get("success"):
        create_local_branch(str(repo_path), issue.get("number", args.issue))

    pr_summary = artifacts_result.get("pr_summary", {}) if artifacts_result.get("success") else {}
    print("\nPR Summary")
    if isinstance(pr_summary, dict):
        if pr_summary.get("pr_title"):
            print(f"Title: {pr_summary['pr_title']}")
        if pr_summary.get("pr_body"):
            print("Body:")
            print(pr_summary["pr_body"])
        if pr_summary.get("files_changed"):
            print("Files changed:")
            for item in pr_summary["files_changed"]:
                if isinstance(item, dict):
                    print(f"- {item.get('file', '<unknown>')}: {item.get('why', '')}")
    else:
        print("No structured summary returned.")

    if agent_result.get("iteration_limit_reached"):
        print(agent_result.get("warning", "Agent stopped at the safety limit."), file=sys.stderr)

    return 0



def _handle_list_issues(args: argparse.Namespace) -> int:
    try:
        owner, repo = args.repo.split("/", 1)
    except ValueError:
        print("Repository must be in owner/repo form.", file=sys.stderr)
        return 1

    result = list_issues(owner, repo)
    if not result.get("success"):
        print(result.get("error", "Failed to list issues"), file=sys.stderr)
        return 1

    issues = result.get("issues", [])
    for issue in issues:
        if isinstance(issue, dict):
            print(f"#{issue.get('number')}: {issue.get('title', '')}")
    return 0


def _handle_show_output(args: argparse.Namespace) -> int:
    issue_dir = PROJECT_ROOT / "outputs" / str(args.issue)
    diff_path = issue_dir / "diff.patch"
    summary_path = issue_dir / "pr_summary.json"

    if not diff_path.exists() and not summary_path.exists():
        print(f"No output artifacts found for issue {args.issue}", file=sys.stderr)
        return 1

    if diff_path.exists():
        print(f"Diff: {diff_path}")
        print(diff_path.read_text(encoding="utf-8"))

    if summary_path.exists():
        print(f"Summary: {summary_path}")
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = summary_path.read_text(encoding="utf-8")
        _print_json_section("PR Summary", summary)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="go-issue-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve_parser = subparsers.add_parser("solve", help="Solve an approved repository issue")
    solve_parser.add_argument("--issue", required=True, help="GitHub issue number")
    solve_parser.add_argument("--repo", required=True, help="Approved repository in owner/repo form")

    list_parser = subparsers.add_parser("list-issues", help="List GitHub issues")
    list_parser.add_argument("--repo", required=True, help="Repository in owner/repo form")

    show_parser = subparsers.add_parser("show-output", help="Show generated issue artifacts")
    show_parser.add_argument("--issue", required=True, help="GitHub issue number")
    return parser


def main() -> int:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "solve":
        return _handle_solve(args)
    if args.command == "list-issues":
        return _handle_list_issues(args)
    if args.command == "show-output":
        return _handle_show_output(args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
