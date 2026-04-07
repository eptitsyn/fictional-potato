"""
LangChain chain construction for code review.
Uses ChatOpenAI with a configurable base_url so any OpenAI-compatible
endpoint (Ollama, Mistral, Azure, etc.) works transparently.
"""
from __future__ import annotations

import json
import math
import re
import textwrap
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from app.core.exceptions import LLMServiceError
from app.core.network import resolve_endpoint_base_url
from app.models.llm import LLMModel
from app.models.prompt import Prompt

# Max chars to send per chunk (rough token-to-char ratio of ~4)
_CHARS_PER_TOKEN = 4
_SAFETY_FACTOR = 0.85  # leave headroom for prompt overhead
_MIN_PROMPTABLE_TOKENS = 64
_RESPONSE_TOKENS_RATIO = 0.18
_MIN_RESPONSE_TOKENS = 256
_MAX_RESPONSE_TOKENS = 2048
_RUSSIAN_OUTPUT_CONSTRAINT = textwrap.dedent("""
    Дополнительное требование:
    Отвечай только по-русски.
    Верни только JSON.
    Все значения поля comment должны быть только на русском языке.
    Если замечание относится к нескольким соседним строкам одного непрерывного блока,
    укажи весь диапазон: start_line — первая затронутая строка, end_line — последняя.
    Не своди такой комментарий к одной строке.
""").strip()


def _build_llm(model: LLMModel, api_key: str | None) -> ChatOpenAI:
    kwargs: dict = {
        "model": model.model_name,
        "temperature": model.temperature,
        "base_url": resolve_endpoint_base_url(model.endpoint.base_url),
    }
    if api_key:
        kwargs["api_key"] = api_key
    else:
        # Some local endpoints ignore the key but langchain requires something
        kwargs["api_key"] = "no-key"
    return ChatOpenAI(**kwargs)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def _truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Approximate token-aware truncation for prompt fragments."""
    if max_tokens <= 0 or not text:
        return ""

    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text

    suffix = "\n...[truncated]"
    truncated = text[: max(0, max_chars - len(suffix))].rstrip()
    return f"{truncated}{suffix}"


def _scaled_token_budget(
    max_context_tokens: int,
    ratio: float,
    minimum: int,
    maximum: int,
) -> int:
    scaled = int(max_context_tokens * ratio)
    return max(minimum, min(maximum, scaled))


def _response_token_budget(max_context_tokens: int) -> int:
    return _scaled_token_budget(
        max_context_tokens=max_context_tokens,
        ratio=_RESPONSE_TOKENS_RATIO,
        minimum=_MIN_RESPONSE_TOKENS,
        maximum=_MAX_RESPONSE_TOKENS,
    )


def _available_prompt_tokens(
    max_context_tokens: int,
    fixed_texts: Sequence[str],
    response_tokens: int | None = None,
) -> int:
    """Return the token budget available for diff-like variable input."""
    if response_tokens is None:
        response_tokens = _response_token_budget(max_context_tokens)
    fixed_tokens = sum(_estimate_tokens(text) for text in fixed_texts)
    remaining_tokens = max_context_tokens - fixed_tokens - response_tokens
    promptable_tokens = int(remaining_tokens * _SAFETY_FACTOR)
    if promptable_tokens < _MIN_PROMPTABLE_TOKENS:
        raise LLMServiceError(
            "Configured prompts are too large for the model context window. "
            "Use a larger context window or shorten the prompt text."
        )
    return promptable_tokens


def _chunk_diff(diff: str, max_tokens: int) -> list[str]:
    """Split diff into chunks that fit within the supplied token budget."""
    max_chars = max(1, int(max_tokens * _CHARS_PER_TOKEN))
    if len(diff) <= max_chars:
        return [diff]

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for line in diff.splitlines(keepends=True):
        if len(line) > max_chars:
            if current_chunk:
                chunks.append("".join(current_chunk).rstrip("\n"))
                current_chunk = []
                current_len = 0
            start = 0
            while start < len(line):
                chunks.append(line[start : start + max_chars].rstrip("\n"))
                start += max_chars
            continue

        if current_len + len(line) > max_chars and current_chunk:
            chunks.append("".join(current_chunk).rstrip("\n"))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line)

    if current_chunk:
        chunks.append("".join(current_chunk).rstrip("\n"))

    return chunks


def _chunk_diff_for_prompt(
    diff: str,
    max_context_tokens: int,
    fixed_texts: Sequence[str],
    response_tokens: int | None = None,
) -> list[str]:
    available_tokens = _available_prompt_tokens(
        max_context_tokens=max_context_tokens,
        fixed_texts=fixed_texts,
        response_tokens=response_tokens,
    )
    return _chunk_diff(diff, available_tokens)


def _extract_json(text: str) -> list[dict]:
    """Try to extract a JSON array from LLM output."""
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?", "", text).strip()
    text = text.strip("`").strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Find first [...] block
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    raise LLMServiceError(f"Could not parse LLM output as JSON array. Output: {text[:500]}")


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _coerce_positive_int_from_keys(payload: dict, *keys: str) -> int | None:
    for key in keys:
        number = _coerce_positive_int(payload.get(key))
        if number is not None:
            return number
    return None


def _normalize_comment(c: dict) -> dict:
    line_number = _coerce_positive_int_from_keys(c, "start_line", "line_number")
    line_end = _coerce_positive_int_from_keys(c, "end_line", "line_end")
    if line_number is not None and line_end is not None and line_end < line_number:
        line_number, line_end = line_end, line_number

    return {
        "file_path": c.get("file_path"),
        "line_number": line_number,
        "line_end": line_end,
        "severity": c.get("severity", "info"),
        "comment_body": c.get("comment", c.get("comment_body", "")),
    }


def _deduplicate_comments(comments: list[dict]) -> list[dict]:
    """Remove exact duplicate comments without another LLM pass."""
    deduplicated: list[dict] = []
    seen: set[tuple] = set()

    for raw_comment in comments:
        comment = _normalize_comment(raw_comment)
        key = (
            comment.get("file_path"),
            comment.get("line_number"),
            comment.get("line_end"),
            comment.get("severity", "info"),
            (comment.get("comment_body") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(comment)

    return deduplicated


def _render_review_prompt_template(prompt_content: str, metadata: dict) -> str:
    """Render metadata while leaving the diff placeholder for chunk substitution."""
    prompt_metadata = {**metadata, "diff": "{diff}"}
    try:
        return prompt_content.format(**prompt_metadata)
    except KeyError:
        return prompt_content


def _enforce_russian_output(text: str) -> str:
    text = text.strip()
    if not text:
        return _RUSSIAN_OUTPUT_CONSTRAINT
    return f"{text}\n\n{_RUSSIAN_OUTPUT_CONSTRAINT}"


async def run_review_chain(
    diff: str,
    metadata: dict,
    system_prompt: Prompt | None,
    review_prompt: Prompt,
    model: LLMModel,
    api_key: str | None,
) -> list[dict]:
    """
    Build and run the review chain.

    Returns a list of dicts with file path, start/end line, severity, and comment body.
    """
    llm = _build_llm(model, api_key)
    parser = StrOutputParser()

    system_text = _enforce_russian_output(system_prompt.content) if system_prompt else _enforce_russian_output(
        textwrap.dedent("""
            Ты опытный ревьюер кода.
            Отвечай только по-русски.
            Верни только корректный JSON-массив.
            Без markdown, без пояснений вне JSON.
            Все тексты комментариев должны быть только на русском языке.
        """).strip()
    )

    # Format the review prompt with metadata
    human_text = _enforce_russian_output(
        _render_review_prompt_template(review_prompt.content, metadata)
    )

    prompt_without_diff = human_text.replace("{diff}", "")
    chunks = _chunk_diff_for_prompt(
        diff=diff,
        max_context_tokens=model.max_context_tokens,
        fixed_texts=[system_text, prompt_without_diff],
    )
    all_comments: list[dict] = []

    for i, chunk in enumerate(chunks):
        chunk_human = human_text.replace("{diff}", chunk)
        if len(chunks) > 1:
            chunk_human += f"\n\n[Это фрагмент {i+1}/{len(chunks)} диффа]"

        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=chunk_human),
        ]

        chain = llm | parser
        raw_output = await chain.ainvoke(messages)
        comments = _extract_json(raw_output)

        # Normalize and validate
        for c in comments:
            all_comments.append(_normalize_comment(c))

    return all_comments
