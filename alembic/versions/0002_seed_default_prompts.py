"""Seed default prompts

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-01 00:01:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

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

Example:
[
  {{"file_path": "src/auth.py", "start_line": 42, "end_line": 42, "severity": "error", "comment": "SQL injection risk: use parameterized queries instead of string formatting"}},
  {{"file_path": "src/auth.py", "start_line": 80, "end_line": 83, "severity": "warning", "comment": "These lines repeat validation logic that should live in one helper to avoid drift"}},
  {{"file_path": null, "start_line": null, "end_line": null, "severity": "info", "comment": "Overall commit looks good, consider adding unit tests for the new validation logic"}}
]"""

MR_REVIEW_PROMPT = """Review the following merge request diff and provide detailed code review comments.

MR Title: {mr_title}
MR Description: {mr_description}
Repository: {repository_name}
Source branch: {source_branch} → Target: {target_branch}

Diff:
{diff}

Respond with a JSON array. Each element must have exactly these fields:
- "file_path": string or null
- "start_line": integer or null
- "end_line": integer or null
- "severity": "info" | "warning" | "error"
- "comment": string

Focus on blocking issues (severity: error) vs suggestions (info/warning)."""


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO prompts (id, user_id, prompt_type, name, content, is_default, is_active, created_at, updated_at)
            VALUES
              (gen_random_uuid(), NULL, 'system', 'Default System Prompt', :system_content, true, true, NOW(), NOW()),
              (gen_random_uuid(), NULL, 'commit_review', 'Default Commit Review', :commit_content, true, true, NOW(), NOW()),
              (gen_random_uuid(), NULL, 'mr_review', 'Default MR Review', :mr_content, true, true, NOW(), NOW())
            """
        ),
        {
            "system_content": SYSTEM_PROMPT,
            "commit_content": COMMIT_REVIEW_PROMPT,
            "mr_content": MR_REVIEW_PROMPT,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM prompts WHERE user_id IS NULL AND is_default = true")
    )
