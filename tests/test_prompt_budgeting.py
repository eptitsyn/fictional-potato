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


SYSTEM_PROMPT = """Ты опытный ревьюер кода. Твоя задача — анализировать изменения в коде и давать
конструктивную, применимую обратную связь на русском языке. Сосредоточься на:
- корректности и возможных багах
- уязвимостях безопасности
- проблемах производительности
- стиле кода и поддерживаемости
- покрытии тестами

Всегда пиши уважительно и по делу. Объясняй, ПОЧЕМУ это проблема, а не только то, что это проблема.
Отвечай только корректным JSON-массивом объектов комментариев. Без markdown и без пояснений вне JSON."""


COMMIT_REVIEW_PROMPT = """Проведи ревью следующего git commit diff и подготовь подробные комментарии к ревью кода.

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
