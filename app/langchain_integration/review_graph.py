"""
LangGraph-based code review agent.

Graph flow:
  START
   │
   ▼
 plan_review          ← Analyze diff, identify files and concern areas
   │
   ▼
 security_review      ← Run security-focused sub-review
   │
   ▼
 quality_review       ← Run code quality / style sub-review
   │
   ▼
 consolidate          ← Merge + deduplicate + prioritize all comments
   │
   ▼
  END  →  list[dict]

Each node is an LLM call. State is a TypedDict passed through the graph.
"""
from __future__ import annotations

import json
import logging
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from app.core.exceptions import LLMServiceError
from app.langchain_integration.chains import (
    _available_prompt_tokens,
    _build_llm,
    _chunk_diff,
    _deduplicate_comments,
    _enforce_russian_output,
    _extract_json,
    _normalize_comment,
    _scaled_token_budget,
    _truncate_to_token_budget,
)
from app.models.llm import LLMModel
from app.models.prompt import Prompt

logger = logging.getLogger(__name__)

_GRAPH_RESPONSE_RATIO = 0.14
_MIN_GRAPH_RESPONSE_TOKENS = 192
_MAX_GRAPH_RESPONSE_TOKENS = 1536
_PLAN_CONTEXT_RATIO = 0.10
_MIN_PLAN_CONTEXT_TOKENS = 128
_MAX_PLAN_CONTEXT_TOKENS = 1024


class ReviewState(TypedDict):
    diff: str
    metadata: dict
    system_prompt: str
    # Per-agent outputs
    plan: str
    security_comments: list[dict]
    quality_comments: list[dict]
    final_comments: list[dict]


def _make_node(llm: ChatOpenAI, system: str, human_template: str):
    """Factory that returns a LangGraph node callable."""
    parser = StrOutputParser()
    chain = llm | parser

    async def node(state: ReviewState) -> dict:
        try:
            human = human_template.format(**state)
        except KeyError:
            human = human_template

        messages = [SystemMessage(content=system), HumanMessage(content=human)]
        return await chain.ainvoke(messages)

    return node


async def run_review_graph(
    diff: str,
    metadata: dict,
    system_prompt: Prompt | None,
    review_prompt: Prompt,
    model: LLMModel,
    api_key: str | None,
) -> list[dict]:
    """
    Run the multi-step LangGraph review pipeline.
    Falls back to single-shot review on graph errors.
    """
    llm = _build_llm(model, api_key)
    parser = StrOutputParser()
    chain = llm | parser

    system_text = (
        _enforce_russian_output(system_prompt.content)
        if system_prompt
        else (
            "Ты опытный ревьюер кода. "
            "Отвечай только по-русски. "
            "Верни только корректный JSON без текста вне JSON. "
            "Все значения поля comment должны быть только на русском языке."
        )
    )
    graph_response_tokens = _scaled_token_budget(
        max_context_tokens=model.max_context_tokens,
        ratio=_GRAPH_RESPONSE_RATIO,
        minimum=_MIN_GRAPH_RESPONSE_TOKENS,
        maximum=_MAX_GRAPH_RESPONSE_TOKENS,
    )
    plan_context_tokens = _scaled_token_budget(
        max_context_tokens=model.max_context_tokens,
        ratio=_PLAN_CONTEXT_RATIO,
        minimum=_MIN_PLAN_CONTEXT_TOKENS,
        maximum=_MAX_PLAN_CONTEXT_TOKENS,
    )

    plan_fixed_text = """Проанализируй этот дифф и перечисли:
1. Какие файлы изменены и за что они отвечают
2. Главные 3-5 направления для ревью (безопасность, производительность, логика, стиль, тесты)
3. Явные тревожные сигналы

Дифф:

Ответь кратким текстом на русском языке, не JSON."""
    review_plan_placeholder = "x" * (plan_context_tokens * 4)
    security_fixed_text = f"""Проведи ревью только на предмет проблем безопасности:
- Инъекции (SQL, command, XSS, path traversal)
- Ошибки auth/authz, отсутствующие проверки
- Секреты и учетные данные в коде
- Небезопасная десериализация или криптография
- OWASP Top 10

Контекст плана ревью:
{review_plan_placeholder}

Дифф:

Верни только JSON-массив. Каждый элемент: {{"file_path": str|null, "start_line": int|null, "end_line": int|null, "severity": "info"|"warning"|"error", "comment": str}}
Все значения поля comment должны быть только на русском языке."""
    quality_fixed_text = f"""Проведи ревью качества кода, поддерживаемости и корректности:
- Баги и ошибки логики
- Проблемы производительности (N+1 запросы, лишние циклы, отсутствующие индексы)
- Отсутствующая обработка ошибок
- Мертвый код и неиспользуемые переменные
- Отсутствующие или слабые тесты
- Проблемы дизайна API

Контекст плана ревью:
{review_plan_placeholder}

Дифф:

Верни только JSON-массив. Каждый элемент: {{"file_path": str|null, "start_line": int|null, "end_line": int|null, "severity": "info"|"warning"|"error", "comment": str}}
Все значения поля comment должны быть только на русском языке."""

    graph_chunk_budget = min(
        _available_prompt_tokens(
            max_context_tokens=model.max_context_tokens,
            fixed_texts=[
                "Ты ведущий инженер и планируешь код-ревью. Отвечай только по-русски.",
                plan_fixed_text,
            ],
            response_tokens=graph_response_tokens,
        ),
        _available_prompt_tokens(
            max_context_tokens=model.max_context_tokens,
            fixed_texts=[system_text, security_fixed_text],
            response_tokens=graph_response_tokens,
        ),
        _available_prompt_tokens(
            max_context_tokens=model.max_context_tokens,
            fixed_texts=[system_text, quality_fixed_text],
            response_tokens=graph_response_tokens,
        ),
    )

    # Chunk diff to the smallest graph-stage budget.
    chunks = _chunk_diff(diff, graph_chunk_budget)
    all_comments: list[dict] = []

    for chunk in chunks:
        state = ReviewState(
            diff=chunk,
            metadata=metadata,
            system_prompt=system_text,
            plan="",
            security_comments=[],
            quality_comments=[],
            final_comments=[],
        )

        try:
            comments = await _run_graph(
                llm,
                chain,
                parser,
                state,
                metadata,
                review_prompt,
                model.max_context_tokens,
                plan_context_tokens,
                graph_response_tokens,
            )
            all_comments.extend(comments)
        except Exception as e:
            logger.warning("LangGraph review failed, falling back to single-shot: %s", e)
            # Fallback: single LLM call
            from app.langchain_integration.chains import run_review_chain
            fallback = await run_review_chain(
                diff=chunk,
                metadata=metadata,
                system_prompt=system_prompt,
                review_prompt=review_prompt,
                model=model,
                api_key=api_key,
            )
            all_comments.extend(fallback)

    return all_comments


async def _run_graph(
    llm,
    chain,
    parser,
    state: ReviewState,
    metadata: dict,
    review_prompt: Prompt,
    max_context_tokens: int,
    plan_context_tokens: int,
    graph_response_tokens: int,
) -> list[dict]:
    diff = state["diff"]
    system_text = state["system_prompt"]

    # ── Node 1: Planning ──────────────────────────────────────────────────────
    plan_prompt = f"""Проанализируй этот дифф и перечисли:
1. Какие файлы изменены и за что они отвечают
2. Главные 3-5 направления для ревью (безопасность, производительность, логика, стиль, тесты)
3. Явные тревожные сигналы

Дифф:
{diff}

Ответь кратким текстом на русском языке, не JSON."""

    plan = await chain.ainvoke([
        SystemMessage(content="Ты ведущий инженер и планируешь код-ревью. Отвечай только по-русски."),
        HumanMessage(content=plan_prompt),
    ])
    plan = _truncate_to_token_budget(plan, plan_context_tokens)

    # ── Node 2: Security Review ────────────────────────────────────────────────
    security_prompt = f"""Проведи ревью только на предмет проблем безопасности:
- Инъекции (SQL, command, XSS, path traversal)
- Ошибки auth/authz, отсутствующие проверки
- Секреты и учетные данные в коде
- Небезопасная десериализация или криптография
- OWASP Top 10

Контекст плана ревью:
{plan}

Дифф:
{diff}

Верни только JSON-массив. Каждый элемент: {{"file_path": str|null, "start_line": int|null, "end_line": int|null, "severity": "info"|"warning"|"error", "comment": str}}
Все значения поля comment должны быть только на русском языке."""

    sec_raw = await chain.ainvoke([
        SystemMessage(content=system_text),
        HumanMessage(content=security_prompt),
    ])

    try:
        security_comments = _extract_json(sec_raw)
    except LLMServiceError:
        security_comments = []

    # ── Node 3: Quality Review ─────────────────────────────────────────────────
    quality_prompt = f"""Проведи ревью качества кода, поддерживаемости и корректности:
- Баги и ошибки логики
- Проблемы производительности (N+1 запросы, лишние циклы, отсутствующие индексы)
- Отсутствующая обработка ошибок
- Мертвый код и неиспользуемые переменные
- Отсутствующие или слабые тесты
- Проблемы дизайна API

Контекст плана ревью:
{plan}

Дифф:
{diff}

Верни только JSON-массив. Каждый элемент: {{"file_path": str|null, "start_line": int|null, "end_line": int|null, "severity": "info"|"warning"|"error", "comment": str}}
Все значения поля comment должны быть только на русском языке."""

    qual_raw = await chain.ainvoke([
        SystemMessage(content=system_text),
        HumanMessage(content=quality_prompt),
    ])

    try:
        quality_comments = _extract_json(qual_raw)
    except LLMServiceError:
        quality_comments = []

    # ── Node 4: Consolidation ──────────────────────────────────────────────────
    all_raw = security_comments + quality_comments
    if not all_raw:
        return []

    consolidate_prompt = f"""Ты собрал комментарии код-ревью из нескольких проходов.
Удали точные дубликаты, объедини пересекающиеся комментарии про одну и ту же строку или диапазон строк и проверь корректность severity.
Сохрани все уникальные и практические замечания.

Комментарии для консолидации:
{json.dumps(all_raw, indent=2)}

Верни только итоговый JSON-массив. Каждый элемент: {{"file_path": str|null, "start_line": int|null, "end_line": int|null, "severity": "info"|"warning"|"error", "comment": str}}
Все значения поля comment должны быть только на русском языке."""

    consolidation_system = (
        "Ты опытный редактор, который объединяет комментарии код-ревью. "
        "Отвечай только по-русски и верни только JSON."
    )
    try:
        _available_prompt_tokens(
            max_context_tokens=max_context_tokens,
            fixed_texts=[consolidation_system, consolidate_prompt],
            response_tokens=graph_response_tokens,
        )
    except LLMServiceError:
        return _deduplicate_comments(all_raw)

    final_raw = await chain.ainvoke([
        SystemMessage(content=consolidation_system),
        HumanMessage(content=consolidate_prompt),
    ])

    try:
        final = _extract_json(final_raw)
    except LLMServiceError:
        final = _deduplicate_comments(all_raw)

    # Normalize
    normalized = []
    for c in final:
        sev = c.get("severity", "info")
        if sev not in ("info", "warning", "error"):
            sev = "info"
        normalized.append(_normalize_comment({**c, "severity": sev}))

    return normalized
