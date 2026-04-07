"""Localize default prompts to Russian

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-07 22:10:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

RUSSIAN_SYSTEM_PROMPT = """Ты опытный ревьюер кода. Твоя задача — анализировать изменения в коде и давать конструктивную, применимую обратную связь на русском языке. Сосредоточься на:
- корректности и возможных багах
- уязвимостях безопасности
- проблемах производительности
- стиле кода и поддерживаемости
- покрытии тестами

Всегда пиши уважительно и по делу. Объясняй, ПОЧЕМУ это проблема, а не только то, что это проблема.
Отвечай только корректным JSON-массивом объектов комментариев.
Без markdown, без пояснений вне JSON.
Все значения поля "comment" должны быть только на русском языке."""

RUSSIAN_COMMIT_REVIEW_PROMPT = """Проведи ревью следующего git commit diff и подготовь подробные комментарии к ревью кода.

Commit SHA: {commit_sha}
Репозиторий: {repository_name}

Дифф:
{diff}

Верни JSON-массив. Каждый элемент должен содержать ровно следующие поля:
- "file_path": string или null (путь к файлу)
- "start_line": integer или null (первая строка в новой версии файла)
- "end_line": integer или null (последняя строка непрерывного диапазона; для одной строки укажи то же значение, что и в start_line)
- "severity": "info" | "warning" | "error"
- "comment": string (текст комментария, конкретный и применимый, только на русском языке)

Пример:
[
  {{"file_path": "src/auth.py", "start_line": 42, "end_line": 42, "severity": "error", "comment": "Риск SQL-инъекции: вместо форматирования строки используй параметризованные запросы."}},
  {{"file_path": "src/auth.py", "start_line": 80, "end_line": 83, "severity": "warning", "comment": "В этих строках дублируется логика валидации. Лучше вынести ее в один helper, чтобы избежать расхождения поведения."}},
  {{"file_path": null, "start_line": null, "end_line": null, "severity": "info", "comment": "В целом изменение выглядит нормально, но стоит добавить unit-тесты для новой логики валидации."}}
]"""

RUSSIAN_MR_REVIEW_PROMPT = """Проведи ревью следующего diff merge request и подготовь подробные комментарии к ревью кода.

Заголовок MR: {mr_title}
Описание MR: {mr_description}
Репозиторий: {repository_name}
Исходная ветка: {source_branch} → Целевая ветка: {target_branch}

Дифф:
{diff}

Верни JSON-массив. Каждый элемент должен содержать ровно следующие поля:
- "file_path": string или null
- "start_line": integer или null
- "end_line": integer или null
- "severity": "info" | "warning" | "error"
- "comment": string (только на русском языке)

Разделяй блокирующие проблемы (severity: error) и рекомендации (info/warning)."""

PREVIOUS_SYSTEM_PROMPT = """You are an expert code reviewer. Your job is to review code changes and provide
constructive, actionable feedback. Focus on:
- Correctness and potential bugs
- Security vulnerabilities
- Performance issues
- Code style and maintainability
- Test coverage

Always be respectful and constructive. Explain WHY something is an issue, not just that it is.
Respond ONLY with a valid JSON array of comment objects. No markdown, no explanation outside JSON."""

PREVIOUS_COMMIT_REVIEW_PROMPT = """Review the following git commit diff and provide detailed code review comments.

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

PREVIOUS_MR_REVIEW_PROMPT = """Review the following merge request diff and provide detailed code review comments.

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


def _update_prompt(connection, prompt_type: str, old_content: str, new_content: str) -> None:
    connection.execute(
        sa.text(
            """
            UPDATE prompts
            SET content = :new_content, updated_at = NOW()
            WHERE user_id IS NULL
              AND is_default = true
              AND prompt_type = :prompt_type
              AND content = :old_content
            """
        ),
        {
            "prompt_type": prompt_type,
            "old_content": old_content,
            "new_content": new_content,
        },
    )


def upgrade() -> None:
    connection = op.get_bind()
    _update_prompt(connection, "system", PREVIOUS_SYSTEM_PROMPT, RUSSIAN_SYSTEM_PROMPT)
    _update_prompt(
        connection,
        "commit_review",
        PREVIOUS_COMMIT_REVIEW_PROMPT,
        RUSSIAN_COMMIT_REVIEW_PROMPT,
    )
    _update_prompt(
        connection,
        "mr_review",
        PREVIOUS_MR_REVIEW_PROMPT,
        RUSSIAN_MR_REVIEW_PROMPT,
    )


def downgrade() -> None:
    connection = op.get_bind()
    _update_prompt(connection, "system", RUSSIAN_SYSTEM_PROMPT, PREVIOUS_SYSTEM_PROMPT)
    _update_prompt(
        connection,
        "commit_review",
        RUSSIAN_COMMIT_REVIEW_PROMPT,
        PREVIOUS_COMMIT_REVIEW_PROMPT,
    )
    _update_prompt(
        connection,
        "mr_review",
        RUSSIAN_MR_REVIEW_PROMPT,
        PREVIOUS_MR_REVIEW_PROMPT,
    )
