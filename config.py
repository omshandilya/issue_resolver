"""Shared configuration for go-issue-agent."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

APPROVED_REPOS = {
    "gin-gonic/gin": "repos/gin",
    "spf13/cobra": "repos/cobra",
    "go-playground/validator": "repos/validator",
    "golangci/golangci-lint": "repos/golangci-lint",
}

MODEL = "llama-3.3-70b-versatile"
GROQ_API_BASE = "https://api.groq.com/openai/v1"  # used by agent/core.py and agent/output.py
MAX_TOOL_ITERATIONS = 12
MAX_FILES_READ = 6
MAX_TOKENS_PER_FILE = 2000
MAX_TOKENS = 4096  # max LLM response tokens; 256 was too small for code fixes