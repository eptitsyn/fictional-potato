"""Prefer line ranges in default prompts

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-07 22:35:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

PREVIOUS_RUSSIAN_SYSTEM_PROMPT = """Ты опытный ревьюер кода. Твоя задача — анализировать изменения в коде и давать конструктивную, применимую обратную связь на русском языке. Сосредоточься на:
- корректности и возможных багах
- уязвимостях безопасности
- проблемах производительности
- стиле кода и поддерживаемости
- покрытии тестами

Всегда пиши уважительно и по делу. Объясняй, ПОЧЕМУ это проблема, а не только то, что это проблема.
Отвечай только корректным JSON-массивом объектов комментариев.
Без markdown, без пояснений вне JSON.
Все значения поля "comment" должны быть только на русском языке."""

RUSSIAN_SYSTEM_PROMPT = """Ты опытный ревьюер кода. Твоя задача — анализировать изменения в коде и давать конструктивную, применимую обратную связь на русском языке. Сосредоточься на:
- корректности и возможных багах
- уязвимостях безопасности
- проблемах производительности
- стиле кода и поддерживаемости
- покрытии тестами

Всегда пиши уважительно и по делу. Объясняй, ПОЧЕМУ это проблема, а не только то, что это проблема.
Отвечай только корректным JSON-массивом объектов комментариев.
Без markdown, без пояснений вне JSON.
Все значения поля "comment" должны быть только на русском языке.
Если замечание относится к нескольким соседним строкам одного непрерывного блока, указывай весь диапазон строк от первой до последней. Не своди такие замечания к одной строке."""

PREVIOUS_RUSSIAN_COMMIT_REVIEW_PROMPT = """Проведи ревью следующего git commit diff и подготовь подробные комментарии к ревью кода.

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

Если замечание относится к нескольким соседним строкам одного непрерывного блока, предпочитай диапазон строк от первой до последней, а не комментарий к одной строке.

Пример:
[
  {{"file_path": "src/auth.py", "start_line": 42, "end_line": 42, "severity": "error", "comment": "Риск SQL-инъекции: вместо форматирования строки используй параметризованные запросы."}},
  {{"file_path": "src/auth.py", "start_line": 80, "end_line": 83, "severity": "warning", "comment": "В этих строках дублируется логика валидации. Лучше вынести ее в один helper, чтобы избежать расхождения поведения."}},
  {{"file_path": null, "start_line": null, "end_line": null, "severity": "info", "comment": "В целом изменение выглядит нормально, но стоит добавить unit-тесты для новой логики валидации."}}
]"""

PREVIOUS_RUSSIAN_MR_REVIEW_PROMPT = """Проведи ревью следующего diff merge request и подготовь подробные комментарии к ревью кода.

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

Если замечание относится к нескольким соседним строкам одного непрерывного блока, предпочитай диапазон строк от первой до последней, а не комментарий к одной строке.
Разделяй блокирующие проблемы (severity: error) и рекомендации (info/warning)."""


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
    _update_prompt(connection, "system", PREVIOUS_RUSSIAN_SYSTEM_PROMPT, RUSSIAN_SYSTEM_PROMPT)
    _update_prompt(
        connection,
        "commit_review",
        PREVIOUS_RUSSIAN_COMMIT_REVIEW_PROMPT,
        RUSSIAN_COMMIT_REVIEW_PROMPT,
    )
    _update_prompt(
        connection,
        "mr_review",
        PREVIOUS_RUSSIAN_MR_REVIEW_PROMPT,
        RUSSIAN_MR_REVIEW_PROMPT,
    )


def downgrade() -> None:
    connection = op.get_bind()
    _update_prompt(connection, "system", RUSSIAN_SYSTEM_PROMPT, PREVIOUS_RUSSIAN_SYSTEM_PROMPT)
    _update_prompt(
        connection,
        "commit_review",
        RUSSIAN_COMMIT_REVIEW_PROMPT,
        PREVIOUS_RUSSIAN_COMMIT_REVIEW_PROMPT,
    )
    _update_prompt(
        connection,
        "mr_review",
        RUSSIAN_MR_REVIEW_PROMPT,
        PREVIOUS_RUSSIAN_MR_REVIEW_PROMPT,
    )
