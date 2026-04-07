"""Add review comment line end

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-07 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OLD_COMMIT_REVIEW_PROMPT = """Review the following git commit diff and provide detailed code review comments.

Commit SHA: {commit_sha}
Repository: {repository_name}

Diff:
{diff}

Respond with a JSON array. Each element must have exactly these fields:
- "file_path": string or null (path to the file)
- "line_number": integer or null (line number in the new file)
- "severity": "info" | "warning" | "error"
- "comment": string (your review comment, be specific and actionable)

Example:
[
  {{"file_path": "src/auth.py", "line_number": 42, "severity": "error", "comment": "SQL injection risk: use parameterized queries instead of string formatting"}},
  {{"file_path": null, "line_number": null, "severity": "info", "comment": "Overall commit looks good, consider adding unit tests for the new validation logic"}}
]"""

NEW_COMMIT_REVIEW_PROMPT = """Review the following git commit diff and provide detailed code review comments.

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

OLD_MR_REVIEW_PROMPT = """Review the following merge request diff and provide detailed code review comments.

MR Title: {mr_title}
MR Description: {mr_description}
Repository: {repository_name}
Source branch: {source_branch} → Target: {target_branch}

Diff:
{diff}

Respond with a JSON array. Each element must have exactly these fields:
- "file_path": string or null
- "line_number": integer or null
- "severity": "info" | "warning" | "error"
- "comment": string

Focus on blocking issues (severity: error) vs suggestions (info/warning)."""

NEW_MR_REVIEW_PROMPT = """Review the following merge request diff and provide detailed code review comments.

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
    op.add_column("review_comments", sa.Column("line_end", sa.Integer(), nullable=True))
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE prompts
            SET content = :new_content, updated_at = NOW()
            WHERE user_id IS NULL
              AND is_default = true
              AND prompt_type = 'commit_review'
              AND content = :old_content
            """
        ),
        {"old_content": OLD_COMMIT_REVIEW_PROMPT, "new_content": NEW_COMMIT_REVIEW_PROMPT},
    )
    connection.execute(
        sa.text(
            """
            UPDATE prompts
            SET content = :new_content, updated_at = NOW()
            WHERE user_id IS NULL
              AND is_default = true
              AND prompt_type = 'mr_review'
              AND content = :old_content
            """
        ),
        {"old_content": OLD_MR_REVIEW_PROMPT, "new_content": NEW_MR_REVIEW_PROMPT},
    )


def downgrade() -> None:
    op.drop_column("review_comments", "line_end")
