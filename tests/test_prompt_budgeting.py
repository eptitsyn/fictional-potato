import pytest

from app.core.exceptions import LLMServiceError
from app.langchain_integration.chains import (
    _available_prompt_tokens,
    _chunk_diff_for_prompt,
    _deduplicate_comments,
    _render_review_prompt_template,
    _response_token_budget,
    _scaled_token_budget,
    _truncate_to_token_budget,
)


SYSTEM_PROMPT = """You are an expert code reviewer. Your job is to review code changes and provide
constructive, actionable feedback. Focus on:
- Correctness and potential bugs
- Security vulnerabilities
- Performance issues
- Code style and maintainability
- Test coverage

Always be respectful and constructive. Explain WHY something is an issue, not just that it is.
Respond ONLY with a valid JSON array of comment objects. No markdown, no explanation outside JSON."""


COMMIT_REVIEW_PROMPT = """Review the following git commit diff and provide detailed code review comments.

Commit SHA: {commit_sha}
Repository: {repository_name}

Diff:
{diff}

Respond with a JSON array. Each element must have exactly these fields:
- "file_path": string or null (path to the file)
- "start_line": integer or null (first line number in the new file)
- "end_line": integer or null (last line number for a continuous range; use the same value as start_line for a single line)
- "severity": "info" | "warning" | "error"
- "comment": string (your review comment, be specific and actionable)
"""


def test_chunk_diff_for_prompt_reserves_space_for_prompt_overhead():
    prompt_without_diff = COMMIT_REVIEW_PROMPT.replace("{diff}", "")
    diff = "\n".join(
        f"--- a/file_{index}.py\n+++ b/file_{index}.py\n@@ -1 +1 @@\n-print('old')\n+print('new')"
        for index in range(250)
    )

    chunks = _chunk_diff_for_prompt(
        diff=diff,
        max_context_tokens=4000,
        fixed_texts=[SYSTEM_PROMPT, prompt_without_diff],
    )
    available_tokens = _available_prompt_tokens(
        max_context_tokens=4000,
        fixed_texts=[SYSTEM_PROMPT, prompt_without_diff],
    )
    max_chars_per_chunk = available_tokens * 4

    assert len(chunks) > 1
    assert all(len(chunk) <= max_chars_per_chunk for chunk in chunks)


def test_available_prompt_tokens_raises_when_fixed_prompt_exceeds_context():
    huge_prompt = "x" * (5000 * 4)

    with pytest.raises(LLMServiceError):
        _available_prompt_tokens(
            max_context_tokens=4000,
            fixed_texts=[huge_prompt],
        )


def test_response_token_budget_scales_with_model_context():
    assert _response_token_budget(4000) < _response_token_budget(32000)


def test_scaled_token_budget_respects_min_and_max_bounds():
    assert _scaled_token_budget(1000, ratio=0.1, minimum=128, maximum=1024) == 128
    assert _scaled_token_budget(50000, ratio=0.1, minimum=128, maximum=1024) == 1024


def test_render_review_prompt_template_keeps_diff_placeholder():
    rendered = _render_review_prompt_template(
        COMMIT_REVIEW_PROMPT,
        {
            "commit_sha": "abc123",
            "repository_name": "repo",
            "diff": "--- a/file.py\n+++ b/file.py",
        },
    )

    assert "Commit SHA: abc123" in rendered
    assert "Repository: repo" in rendered
    assert "{diff}" in rendered
    assert "--- a/file.py" not in rendered


def test_truncate_to_token_budget_marks_truncation():
    source = "a" * 200

    truncated = _truncate_to_token_budget(source, max_tokens=20)

    assert truncated.endswith("...[truncated]")
    assert len(truncated) < len(source)


def test_deduplicate_comments_normalizes_exact_duplicates():
    comments = [
        {
            "file_path": "app/example.py",
            "start_line": 10,
            "end_line": 10,
            "severity": "warning",
            "comment": "Handle the None case explicitly.",
        },
        {
            "file_path": "app/example.py",
            "line_number": 10,
            "line_end": 10,
            "severity": "warning",
            "comment_body": "Handle the None case explicitly.",
        },
    ]

    deduplicated = _deduplicate_comments(comments)

    assert deduplicated == [
        {
            "file_path": "app/example.py",
            "line_number": 10,
            "line_end": 10,
            "severity": "warning",
            "comment_body": "Handle the None case explicitly.",
        }
    ]


def test_available_prompt_tokens_scales_with_model_context():
    small_budget = _available_prompt_tokens(
        max_context_tokens=4000,
        fixed_texts=[SYSTEM_PROMPT, COMMIT_REVIEW_PROMPT.replace("{diff}", "")],
    )
    large_budget = _available_prompt_tokens(
        max_context_tokens=32000,
        fixed_texts=[SYSTEM_PROMPT, COMMIT_REVIEW_PROMPT.replace("{diff}", "")],
    )

    assert small_budget < large_budget
