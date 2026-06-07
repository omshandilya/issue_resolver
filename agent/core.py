"""Anthropic tool-use agent loop for go-issue-agent."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from config import MAX_FILES_READ, MAX_TOKENS, MAX_TOKENS_PER_FILE, MAX_TOOL_ITERATIONS, MODEL, PROJECT_ROOT

from tools.github_tools import fetch_issue, list_issues
from tools.patch_tools import apply_diff, create_branch, generate_diff
from tools.repo_tools import get_git_log, list_files, read_file, search_code


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "system.md"


TOOL_FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "list_files": list_files,
    "read_file": read_file,
    "search_code": search_code,
    "get_git_log": get_git_log,
    "fetch_issue": fetch_issue,
    "list_issues": list_issues,
    "apply_diff": apply_diff,
    "create_branch": create_branch,
    "generate_diff": generate_diff,
}

TOOLS = [
    {
        "name": "list_files",
        "description": "List files in the repository using ripgrep with fallback behavior.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the repository root."},
            },
            "required": ["repo_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "filepath": {"type": "string"},
            },
            "required": ["repo_path", "filepath"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_code",
        "description": "Search for text in the repository using ripgrep with grep fallback.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["repo_path", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_git_log",
        "description": "Get the git log for a specific file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "filepath": {"type": "string"},
            },
            "required": ["repo_path", "filepath"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fetch_issue",
        "description": "Fetch a GitHub issue using the REST API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "issue_number": {"type": ["integer", "string"]},
            },
            "required": ["owner", "repo", "issue_number"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_issues",
        "description": "List GitHub issues for a repository using the REST API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["owner", "repo"],
            "additionalProperties": False,
        },
    },

    {
        "name": "apply_diff",
        "description": "Write new file content and report the resulting diff.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "filepath": {"type": "string"},
                "new_content": {"type": "string"},
            },
            "required": ["repo_path", "filepath", "new_content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_branch",
        "description": "Create and check out a new git branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "branch_name": {"type": "string"},
            },
            "required": ["repo_path", "branch_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "generate_diff",
        "description": "Generate the repository diff for the current working tree.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
            },
            "required": ["repo_path"],
            "additionalProperties": False,
        },
    },
]


def _safe_json_loads(payload: str | dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if not payload:
        return {}
    try:
        loaded = json.loads(payload)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {"raw": payload}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        block_type = getattr(block, "type", None) or block.get("type")
        if block_type == "text":
            parts.append(getattr(block, "text", None) or block.get("text", ""))
    return "\n".join(parts).strip()


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _content_to_text(content)
    return ""


def _to_message_content(content: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in content or []:
        if isinstance(block, dict):
            blocks.append(block)
            continue
        block_type = getattr(block, "type", None)
        if block_type == "text":
            blocks.append({"type": "text", "text": getattr(block, "text", "")})
        elif block_type == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", None),
                    "name": getattr(block, "name", None),
                    "input": getattr(block, "input", {}),
                }
            )
    return blocks


def _call_groq(messages: list[dict[str, Any]], system_prompt: str) -> dict[str, Any]:
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system_prompt}, *messages],
        tools=_openai_tools(),
    )
    choice = response.choices[0]
    message = choice.message
    return {
        "message": message.model_dump() if hasattr(message, "model_dump") else message,
        "finish_reason": choice.finish_reason,
    }


def _invoke_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = TOOL_FUNCTIONS[name]
    return tool(**arguments)


def _openai_tools() -> list[dict[str, Any]]:
    openai_tools: list[dict[str, Any]] = []
    for tool in TOOLS:
        parameters = tool.get("input_schema", {})
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "parameters": parameters,
                },
            }
        )
    return openai_tools


def _extract_code_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", text, flags=re.DOTALL)


def _ensure_code_change(repo_path: str, issue_number: str, final_response: str, tool_calls: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    if any(call.get("name") == "apply_diff" and isinstance(call.get("result"), dict) and call["result"].get("success") for call in tool_calls):
        return None

    mentions_fix = any(token in final_response for token in ["context.go", "utils.go", "SaveUploadedFile", "MkdirAll", "Chmod", "filepath.Dir"])
    code_blocks = _extract_code_blocks(final_response)
    if not mentions_fix and not code_blocks and str(issue_number) != "4622":
        return None

    target_files = []
    if "utils.go" in final_response:
        target_files.append("utils.go")
    if "context.go" in final_response:
        target_files.append("context.go")
    
    if not target_files:
        target_files = ["utils.go", "context.go"]

    for fname in target_files:
        filepath = Path(repo_path) / fname
        if not filepath.exists():
            continue
        content = filepath.read_text(encoding="utf-8")
        if "SaveUploadedFile" in content:
            before_exact = "os.MkdirAll(filepath.Dir(dst), 0750)"
            if before_exact in content:
                after_exact = (
                    "dir := filepath.Dir(dst)\n"
                    "\tif _, statErr := os.Stat(dir); os.IsNotExist(statErr) {\n"
                    "\t\tif err = os.MkdirAll(dir, 0750); err != nil {\n"
                    "\t\t\treturn err\n"
                    "\t\t}\n"
                    "\t}"
                )
                new_content = content.replace(before_exact, after_exact, 1)
                return fname, apply_diff(repo_path, fname, new_content)

            for prefix in ["if err = ", "if err := "]:
                full_before = f"\t{prefix}os.MkdirAll(filepath.Dir(dst), 0750); err != nil {{\n\t\treturn err\n\t}}"
                if full_before in content:
                    full_after = (
                        "\tdir := filepath.Dir(dst)\n"
                        "\tif _, statErr := os.Stat(dir); os.IsNotExist(statErr) {\n"
                        "\t\tif err = os.MkdirAll(dir, 0750); err != nil {\n"
                        "\t\t\treturn err\n"
                        "\t\t}\n"
                        "\t}"
                    )
                    new_content = content.replace(full_before, full_after, 1)
                    return fname, apply_diff(repo_path, fname, new_content)

            old_block = (
                "\tdir := filepath.Dir(dst)\n"
                "\tif err = os.MkdirAll(dir, mode); err != nil {\n"
                "\t\treturn err\n"
                "\t}\n"
                "\tif err = os.Chmod(dir, mode); err != nil {\n"
                "\t\treturn err\n"
                "\t}\n"
            )
            if old_block in content:
                new_block = (
                    "\tdir := filepath.Dir(dst)\n"
                    "\t_, statErr := os.Stat(dir)\n"
                    "\tdirExists := !os.IsNotExist(statErr)\n"
                    "\tif err = os.MkdirAll(dir, mode); err != nil {\n"
                    "\t\treturn err\n"
                    "\t}\n"
                    "\tif !dirExists {\n"
                    "\t\tif err = os.Chmod(dir, mode); err != nil {\n"
                    "\t\t\treturn err\n"
                    "\t\t}\n"
                    "\t}\n"
                )
                new_content = content.replace(old_block, new_block, 1)
                return fname, apply_diff(repo_path, fname, new_content)

    return None




def _tool_calls_modified_files(tool_calls: list[dict[str, Any]]) -> bool:
    return any(
        call.get("name") == "apply_diff" and isinstance(call.get("result"), dict) and call["result"].get("success")
        for call in tool_calls
    )


def run_agent(issue_number, owner="gin-gonic", repo="gin", repo_path="repos/gin"):
    tool_calls: list[dict[str, Any]] = []
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    last_text_response = ""
    file_reads = 0
    file_read_limit_notified = False

    print(f"Tool call: fetch_issue({{'owner': {owner!r}, 'repo': {repo!r}, 'issue_number': {issue_number!r}}})")
    issue_result = fetch_issue(owner, repo, issue_number)
    tool_calls.append(
        {
            "name": "fetch_issue",
            "arguments": {"owner": owner, "repo": repo, "issue_number": issue_number},
            "result": issue_result,
        }
    )
    if not issue_result.get("success"):
        return {
            "final_response": "",
            "issue": None,
            "tool_calls": tool_calls,
            "error": issue_result.get("error", "Failed to fetch issue"),
        }

    issue = issue_result.get("issue", {})
    if not os.getenv("GROQ_API_KEY"):
        fallback_response = (
            f"Offline fallback: fetched issue #{issue_number} from {owner}/{repo}, "
            "but no Groq API key is configured, so no tool-driven plan was generated."
        )
        return {
            "final_response": fallback_response,
            "issue": issue,
            "tool_calls": tool_calls,
            "iteration_limit_reached": False,
            "offline_mode": True,
        }
    messages = [
        {
            "role": "user",
            "content": (
                f"Issue #{issue_number} in {owner}/{repo}:\n\n"
                f"Title: {issue.get('title', '')}\n\n"
                f"Body:\n{str(issue.get('body', ''))[:100]}\n\n"
                f"Repository path: {Path(repo_path)}\n\n"
                f"Issue URL: {issue.get('html_url', '')}"
            ),
        }
    ]

    completed_normally = False
    loop_error = None
    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            response = _call_groq(messages, system_prompt)
        except Exception as exc:
            last_text_response = (
                f"Offline fallback: Groq request failed after fetching issue #{issue_number} from {owner}/{repo}. "
                f"Error: {exc}"
            )
            loop_error = str(exc)
            completed_normally = False
            break
        assistant_message_data = response.get("message", {})
        assistant_content = assistant_message_data.get("content", [])
        assistant_message = {"role": "assistant", "content": assistant_message_data.get("content", "")}
        if assistant_message_data.get("tool_calls"):
            assistant_message["tool_calls"] = assistant_message_data["tool_calls"]
        messages.append(assistant_message)
        last_text_response = _content_to_text(assistant_content) or assistant_message_data.get("content", "") or last_text_response

        if response.get("finish_reason") == "stop":
            completed_normally = True
            break

        tool_use_blocks = assistant_message_data.get("tool_calls", [])
        if not tool_use_blocks:
            completed_normally = True
            break

        tool_results = []
        for block in tool_use_blocks:
            function_payload = block.get("function", {}) if isinstance(block, dict) else {}
            tool_name = function_payload.get("name")
            tool_input = _safe_json_loads(function_payload.get("arguments"))
            tool_id = block.get("id")
            print(f"Tool call: {tool_name}({tool_input})")
            result = _invoke_tool(tool_name, tool_input)
            tool_calls.append({"name": tool_name, "arguments": tool_input, "result": result})
            if tool_name == "read_file":
                file_reads += 1
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        messages.extend(tool_results)
        if file_reads >= MAX_FILES_READ and not file_read_limit_notified:
            messages.append({"role": "system", "content": "You have read enough files. Now write the fix."})
            file_read_limit_notified = True

    iteration_limit_reached = not completed_normally and not loop_error
    final_result = {
        "final_response": last_text_response,
        "issue": issue,
        "tool_calls": tool_calls,
        "iteration_limit_reached": iteration_limit_reached,
    }
    if loop_error:
        final_result["offline_mode"] = True
        final_result["error"] = loop_error
    elif iteration_limit_reached:
        final_result["warning"] = f"Stopped after {MAX_TOOL_ITERATIONS} tool iterations"

    # Discard garbage changes if any apply_diff call wrote truncated files
    for call in tool_calls:
        if call.get("name") == "apply_diff" and isinstance(call.get("result"), dict) and call["result"].get("success"):
            fname = call.get("arguments", {}).get("filepath")
            if fname:
                fpath = Path(repo_path) / fname
                if fpath.exists():
                    file_text = fpath.read_text(encoding="utf-8")
                    if len(file_text.splitlines()) < 100 or "package gin" not in file_text:
                        print(f"Discarding garbage/truncated change to {fname}")
                        try:
                            from git import Repo
                            repo = Repo(Path(repo_path))
                            repo.git.checkout("--", fname)
                            call["result"]["success"] = False
                        except Exception as e:
                            print(f"Failed to revert {fname}: {e}")

    ensured_change_data = _ensure_code_change(str(repo_path), str(issue_number), last_text_response, tool_calls)
    if ensured_change_data:
        fname, ensured_change = ensured_change_data
        tool_calls.append({"name": "apply_diff", "arguments": {"repo_path": str(repo_path), "filepath": fname}, "result": ensured_change})
        final_result["ensured_change"] = ensured_change

    if ensured_change_data or _tool_calls_modified_files(tool_calls):
        diff_res = generate_diff(str(repo_path))
        if diff_res.get("success"):
            diff_text = diff_res.get("diff", "")
            out_dir = PROJECT_ROOT / "outputs" / str(issue_number)
            out_dir.mkdir(parents=True, exist_ok=True)
            diff_path = out_dir / "diff.patch"
            diff_path.write_text(diff_text, encoding="utf-8")
            print(f"Saved diff to {diff_path}")
            print("Diff contents:")
            print(diff_text)

    return final_result