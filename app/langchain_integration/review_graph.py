"""
Agentic code review pipeline — ReAct pattern.

Architecture
────────────
                 ┌──────────────────────────────┐
                 │      OrchestratorAgent        │
                 │  • Plans overall review scope │
                 │  • Dispatches sub-agents      │
                 └───────────┬──────────────────┘
                             │  concurrently
              ┌──────────────┴──────────────┐
              ▼                             ▼
  ┌──────────────────────┐   ┌──────────────────────┐
  │   SecurityAgent      │   │    QualityAgent       │
  │  Goal: OWASP/auth    │   │  Goal: bugs/perf      │
  │  ┌─────────────────┐ │   │  ┌─────────────────┐ │
  │  │ 1. PLAN         │ │   │  │ 1. PLAN         │ │
  │  │ 2. ACT (tools)  │ │   │  │ 2. ACT (tools)  │ │
  │  │ 3. REFLECT      │ │   │  │ 3. REFLECT      │ │
  │  │ 4. → repeat     │ │   │  │ 4. → repeat     │ │
  │  │    until done   │ │   │  │    until done   │ │
  │  └─────────────────┘ │   │  └─────────────────┘ │
  │  Tools:              │   │  Tools:              │
  │    get_file_content  │   │    get_file_content  │
  │    list_directory    │   │    list_directory    │
  └──────────────────────┘   └──────────────────────┘
              │                             │
              └──────────────┬──────────────┘
                             ▼
                 ┌──────────────────────────┐
                 │    ConsolidatorAgent     │
                 │  Deduplicates + ranks   │
                 └──────────────────────────┘

Each sub-agent follows the ReAct loop:
  PLAN  →  (ACT → OBSERVE → REFLECT) × N  →  ANSWER
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from langchain_core.messages import (  # type: ignore[import]
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.output_parsers import (  # type: ignore[import]
    StrOutputParser,
)

from app.core.exceptions import LLMServiceError
from app.langchain_integration.chains import (
    _available_prompt_tokens,
    _build_llm,
    _chunk_diff,
    _deduplicate_comments,
    _enforce_russian_output,
    _extract_json,
    _log_llm_parse_failure,
    _normalize_comment,
    _render_review_prompt_instructions,
    _scaled_token_budget,
    _truncate_to_token_budget,
)
from app.models.llm import LLMModel
from app.models.prompt import Prompt

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI  # type: ignore[import]

    from app.services.gitlab_service import GitLabClient

logger = logging.getLogger(__name__)

# ── Token budgets ────────────────────────────────────────────────────────────

_GRAPH_RESPONSE_RATIO = 0.14
_MIN_GRAPH_RESPONSE_TOKENS = 192
_MAX_GRAPH_RESPONSE_TOKENS = 1536
_PLAN_CONTEXT_RATIO = 0.10
_MIN_PLAN_CONTEXT_TOKENS = 128
_MAX_PLAN_CONTEXT_TOKENS = 1024

# ── Agent loop limits ────────────────────────────────────────────────────────

_MAX_TOOL_ITERATIONS = 8

# ── Shared prompt fragments ──────────────────────────────────────────────────

_LINE_RANGE_GUIDANCE = (
    "Если замечание относится к нескольким соседним строкам одного "
    "непрерывного блока, верни весь диапазон: start_line — первая строка, "
    "end_line — последняя. Не своди такие замечания к одной строке."
)

_COMMENT_SCHEMA = (
    '{"file_path": str|null, "start_line": int|null, '
    '"end_line": int|null, '
    '"severity": "info"|"warning"|"error", "comment": str}'
)

# Limits for stored prompt/response text (chars). Large enough to be useful.
_LOG_PROMPT_LIMIT = 20_000
_LOG_RESPONSE_LIMIT = 10_000


async def _log_llm_exchange(
    *,
    job_id: str | None,
    model_name: str,
    stage: str,
    prompt: str,
    response: str,
) -> None:
    """Log one LLM request+response pair to event_logs."""
    from app.services.event_log_service import log_event

    await log_event(
        None,
        "llm.exchange",
        f"LLM exchange — stage '{stage}'",
        details={
            "job_id": job_id,
            "model": model_name,
            "stage": stage,
            "prompt_chars": len(prompt),
            "response_chars": len(response),
            "prompt": prompt[:_LOG_PROMPT_LIMIT],
            "response": response[:_LOG_RESPONSE_LIMIT],
        },
    )


async def _log_agent_step(
    *,
    job_id: str | None,
    stage: str,
    event_type: str,
    message: str,
    level: str = "info",
    details: dict | None = None,
) -> None:
    """Log a single agent pipeline step (tool call, observation, reflect, etc.)."""
    from app.services.event_log_service import log_event

    await log_event(
        None,
        event_type,
        message,
        level=level,
        details={"job_id": job_id, "stage": stage, **(details or {})},
    )


# ── ReAct agent loop ─────────────────────────────────────────────────────────

def _reflect_prompt(step: int, max_steps: int) -> str:
    """
    Prompt injected after each tool-call round so the agent can decide
    whether to continue gathering context or finalize its findings.
    """
    return (
        f"[Шаг {step}/{max_steps}] "
        "Оцени накопленные данные: достаточно ли контекста для качественных "
        "замечаний?\n"
        "• Если нет — продолжай вызывать инструменты.\n"
        "• Если да — верни финальный JSON-массив замечаний "
        "(без дополнительных вызовов инструментов).\n"
        f"Каждый элемент: {_COMMENT_SCHEMA}"
    )


async def _run_agent(
    llm: "ChatOpenAI",
    tools: list,
    system_message: str,
    goal: str,
    task_prompt: str,
    *,
    max_iterations: int = _MAX_TOOL_ITERATIONS,
    job_id: str | None = None,
    model_name: str = "",
    stage: str = "agent",
) -> str:
    """
    ReAct-style agent loop with planning, memory, and self-reflection.

    Phases
    ──────
    1. PLAN   — LLM produces an explicit plan (no tool calls).
    2. ACT    — LLM calls tools to gather context.
    3. OBSERVE — Tool results are appended to message history (= memory).
    4. REFLECT — LLM decides whether findings are complete.
    5. Repeat ACT→OBSERVE→REFLECT until the agent stops calling tools
       or *max_iterations* is reached.
    6. ANSWER  — Final text response is extracted.

    The full message history is passed on every invocation, so the agent
    retains complete memory of everything it has observed so far.
    """
    parser = StrOutputParser()
    tool_map = {t.name: t for t in tools}

    # ── Phase 1: PLAN ────────────────────────────────────────────────────────
    plan_system = (
        f"{system_message}\n\n"
        f"Твоя задача (цель): {goal}\n\n"
        "На этом шаге только планируй — не вызывай инструменты и не давай "
        "финальный ответ."
    )
    plan_prompt = (
        f"{task_prompt}\n\n"
        "Составь детальный план:\n"
        "1. Какие аспекты ты проверишь в первую очередь?\n"
        "2. Какие файлы тебе понадобятся для полного контекста?\n"
        "3. На что обратишь особое внимание?\n\n"
        "Ответь только планом, без замечаний и без JSON."
    )
    plan_raw = await (llm | parser).ainvoke([
        SystemMessage(content=plan_system),
        HumanMessage(content=plan_prompt),
    ])
    logger.debug("Agent plan: %s", plan_raw[:300])
    await _log_llm_exchange(
        job_id=job_id, model_name=model_name,
        stage=f"{stage}.plan",
        prompt=f"[SYSTEM]\n{plan_system}\n\n[USER]\n{plan_prompt}",
        response=plan_raw,
    )

    # ── Build conversation with plan as memory seed ──────────────────────────
    messages = [
        SystemMessage(content=f"{system_message}\n\nТвоя задача: {goal}"),
        HumanMessage(content=plan_prompt),
        AIMessage(content=f"[Мой план]\n{plan_raw}"),
        HumanMessage(
            content=(
                "Хорошо. Теперь выполни план. "
                "Используй инструменты для получения файлов, которые нужны "
                "для полного анализа. Когда будешь готов — верни JSON."
            )
        ),
    ]

    if not tools:
        # No tools — single-shot answer after planning
        prompt_text = "\n".join(
            f"[{m.__class__.__name__}]\n{m.content}" for m in messages
        )
        answer = await (llm | parser).ainvoke(messages)
        await _log_llm_exchange(
            job_id=job_id, model_name=model_name,
            stage=f"{stage}.act_no_tools",
            prompt=prompt_text,
            response=answer,
        )
        return answer

    llm_with_tools = llm.bind_tools(tools)

    # ── Phases 2-4: ACT → OBSERVE → REFLECT loop ────────────────────────────
    for iteration in range(max_iterations):
        response: AIMessage = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        prompt_text = "\n".join(
            f"[{m.__class__.__name__}]\n{getattr(m, 'content', '')}" for m in messages[:-1]
        )
        await _log_llm_exchange(
            job_id=job_id, model_name=model_name,
            stage=f"{stage}.act_iter_{iteration + 1}",
            prompt=prompt_text,
            response=response.content or f"[tool_calls: {[tc['name'] for tc in tool_calls]}]",
        )

        if not tool_calls:
            # Agent decided it has enough information — no tools, final answer
            logger.debug("Agent finished after %d iteration(s)", iteration + 1)
            await _log_agent_step(
                job_id=job_id, stage=stage,
                event_type="agent.answer",
                message=f"{stage}: agent answered after {iteration + 1} iteration(s)",
                details={
                    "iteration": iteration + 1,
                    "answer_chars": len(response.content or ""),
                    "answer": (response.content or "")[:_LOG_RESPONSE_LIMIT],
                },
            )
            return response.content or ""

        logger.debug(
            "Agent tools (iter %d/%d): %s",
            iteration + 1,
            max_iterations,
            [tc["name"] for tc in tool_calls],
        )

        # ── OBSERVE: execute tools, append results to memory ─────────────────
        for tc in tool_calls:
            await _log_agent_step(
                job_id=job_id, stage=stage,
                event_type="agent.tool_call",
                message=f"{stage}: calling tool '{tc['name']}'",
                details={
                    "iteration": iteration + 1,
                    "tool": tc["name"],
                    "args": tc.get("args", {}),
                },
            )

            tool_fn = tool_map.get(tc["name"])
            if tool_fn is None:
                result = f"[Инструмент '{tc['name']}' не найден]"
                await _log_agent_step(
                    job_id=job_id, stage=stage,
                    event_type="agent.tool_result",
                    message=f"{stage}: tool '{tc['name']}' not found",
                    level="warning",
                    details={"iteration": iteration + 1, "tool": tc["name"], "result": result},
                )
            else:
                try:
                    result = await tool_fn.ainvoke(tc["args"])
                    await _log_agent_step(
                        job_id=job_id, stage=stage,
                        event_type="agent.tool_result",
                        message=f"{stage}: tool '{tc['name']}' returned {len(str(result))} chars",
                        details={
                            "iteration": iteration + 1,
                            "tool": tc["name"],
                            "result_chars": len(str(result)),
                            "result": str(result)[:_LOG_RESPONSE_LIMIT],
                        },
                    )
                except Exception as exc:
                    logger.warning("Tool %s raised: %s", tc["name"], exc)
                    result = f"[Ошибка инструмента '{tc['name']}': {exc}]"
                    await _log_agent_step(
                        job_id=job_id, stage=stage,
                        event_type="agent.tool_result",
                        message=f"{stage}: tool '{tc['name']}' raised an error",
                        level="warning",
                        details={"iteration": iteration + 1, "tool": tc["name"], "error": str(exc)},
                    )

            messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )

        # ── REFLECT: ask agent to assess completeness ────────────────────────
        reflect_text = _reflect_prompt(iteration + 1, max_iterations)
        await _log_agent_step(
            job_id=job_id, stage=stage,
            event_type="agent.reflect",
            message=f"{stage}: reflect step after iteration {iteration + 1}",
            details={
                "iteration": iteration + 1,
                "reflect_prompt": reflect_text,
                "tools_called": [tc["name"] for tc in tool_calls],
            },
        )
        messages.append(HumanMessage(content=reflect_text))

    # ── Max iterations reached — force a final answer ────────────────────────
    logger.warning(
        "Agent reached max_iterations=%d, forcing final answer", max_iterations
    )
    force_msg = (
        "Достигнут лимит шагов. "
        "Верни финальный JSON-массив замечаний "
        "по собранным данным.\n"
        f"Каждый элемент: {_COMMENT_SCHEMA}"
    )
    final_messages = messages + [HumanMessage(content=force_msg)]
    forced = await (llm | parser).ainvoke(final_messages)
    await _log_llm_exchange(
        job_id=job_id, model_name=model_name,
        stage=f"{stage}.forced_final",
        prompt="\n".join(
            f"[{m.__class__.__name__}]\n{getattr(m, 'content', '')}" for m in final_messages
        ),
        response=forced,
    )
    return forced


# ── Orchestrator: planning node ──────────────────────────────────────────────

async def _plan_review(
    llm: "ChatOpenAI",
    diff: str,
    plan_context_tokens: int,
    *,
    job_id: str | None = None,
    model_name: str = "",
) -> str:
    """
    Orchestrator planning node — summarises the diff and identifies
    focus areas for sub-agents. No tools needed here.
    """
    prompt = (
        "Проанализируй этот дифф и перечисли:\n"
        "1. Какие файлы изменены и за что они отвечают\n"
        "2. Главные 3-5 направления для ревью\n"
        "   (безопасность, производительность, логика, стиль, тесты)\n"
        "3. Явные тревожные сигналы\n\n"
        f"Дифф:\n{diff}\n\n"
        "Ответь кратким текстом на русском языке, не JSON."
    )
    plan_system = (
        "Ты ведущий инженер и планируешь код-ревью. "
        "Отвечай только по-русски."
    )
    plan = await (llm | StrOutputParser()).ainvoke([
        SystemMessage(content=plan_system),
        HumanMessage(content=prompt),
    ])
    await _log_llm_exchange(
        job_id=job_id, model_name=model_name,
        stage="orchestrator.plan",
        prompt=f"[SYSTEM]\n{plan_system}\n\n[USER]\n{prompt}",
        response=plan,
    )
    return _truncate_to_token_budget(plan, plan_context_tokens)


# ── Sub-agents ───────────────────────────────────────────────────────────────

async def _security_agent(
    llm: "ChatOpenAI",
    tools: list,
    diff: str,
    plan: str,
    review_guidance: str,
    system_text: str,
    *,
    job_id: str | None = None,
    model_name: str = "",
) -> list[dict]:
    """
    Security sub-agent — ReAct loop focused on OWASP Top 10,
    auth/authz, secrets, injections.
    """
    goal = (
        "Найти все проблемы безопасности в предоставленном диффе: "
        "инъекции (SQL/command/XSS), ошибки auth/authz, секреты в коде, "
        "небезопасная десериализация, OWASP Top 10."
    )
    system = (
        f"{system_text}\n\n"
        "Ты — агент безопасности кода. Используй инструменты, чтобы получить "
        "полный контекст подозрительных мест. "
        "Финальный ответ — только JSON-массив."
    )
    task = (
        f"Контекст плана ревью:\n{plan}\n\n"
        f"Дополнительные инструкции:\n{review_guidance}\n\n"
        f"Дифф:\n{diff}\n\n"
        "Если видишь подозрительный вызов, импорт или логику — запроси "
        "полный файл через get_file_content для точного анализа.\n\n"
        f"Итоговый ответ: JSON-массив. Элемент: {_COMMENT_SCHEMA}\n"
        f"Все comment только по-русски.\n{_LINE_RANGE_GUIDANCE}"
    )
    raw = await _run_agent(
        llm, tools, system, goal, task,
        job_id=job_id, model_name=model_name, stage="security_agent",
    )
    try:
        return _extract_json(raw)
    except LLMServiceError as exc:
        _log_llm_parse_failure(stage="security_agent", output=raw, error=exc)
        return []


async def _quality_agent(
    llm: "ChatOpenAI",
    tools: list,
    diff: str,
    plan: str,
    review_guidance: str,
    system_text: str,
    *,
    job_id: str | None = None,
    model_name: str = "",
) -> list[dict]:
    """
    Quality sub-agent — ReAct loop focused on bugs, performance,
    maintainability, missing tests.
    """
    goal = (
        "Найти все проблемы качества кода: баги, ошибки логики, "
        "N+1 запросы, отсутствующая обработка ошибок, мёртвый код, "
        "слабые тесты, проблемы дизайна API."
    )
    system = (
        f"{system_text}\n\n"
        "Ты — агент качества кода. Используй инструменты, чтобы проверить "
        "связанные модули и полный контекст изменённых функций. "
        "Финальный ответ — только JSON-массив."
    )
    task = (
        f"Контекст плана ревью:\n{plan}\n\n"
        f"Дополнительные инструкции:\n{review_guidance}\n\n"
        f"Дифф:\n{diff}\n\n"
        "Если видишь вызов, который может быть реализован неоптимально или "
        "вызывать баги — запроси полный файл через get_file_content.\n\n"
        f"Итоговый ответ: JSON-массив. Элемент: {_COMMENT_SCHEMA}\n"
        f"Все comment только по-русски.\n{_LINE_RANGE_GUIDANCE}"
    )
    raw = await _run_agent(
        llm, tools, system, goal, task,
        job_id=job_id, model_name=model_name, stage="quality_agent",
    )
    try:
        return _extract_json(raw)
    except LLMServiceError as exc:
        _log_llm_parse_failure(stage="quality_agent", output=raw, error=exc)
        return []


# ── Consolidator ─────────────────────────────────────────────────────────────

async def _consolidate(
    llm: "ChatOpenAI",
    all_comments: list[dict],
    review_guidance: str,
    max_context_tokens: int,
    graph_response_tokens: int,
    *,
    job_id: str | None = None,
    model_name: str = "",
) -> list[dict]:
    """Merge and deduplicate comments from all sub-agents."""
    if not all_comments:
        return []

    system = (
        "Ты опытный редактор, который объединяет комментарии код-ревью. "
        "Отвечай только по-русски и верни только JSON."
    )
    prompt = (
        "Ты собрал комментарии из нескольких агентов.\n"
        "Удали точные дубликаты, объедини пересекающиеся замечания про одну "
        "строку, проверь корректность severity. "
        "Сохрани все уникальные и практические замечания.\n\n"
        "Комментарии для консолидации:\n"
        f"{json.dumps(all_comments, indent=2, ensure_ascii=False)}\n\n"
        f"Дополнительные инструкции:\n{review_guidance}\n\n"
        f"Верни только JSON-массив. Элемент: {_COMMENT_SCHEMA}\n"
        f"Все comment только по-русски.\n{_LINE_RANGE_GUIDANCE}"
    )

    try:
        _available_prompt_tokens(
            max_context_tokens=max_context_tokens,
            fixed_texts=[system, prompt],
            response_tokens=graph_response_tokens,
        )
    except LLMServiceError:
        return _deduplicate_comments(all_comments)

    raw = await (llm | StrOutputParser()).ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=prompt),
    ])
    await _log_llm_exchange(
        job_id=job_id, model_name=model_name,
        stage="consolidator",
        prompt=f"[SYSTEM]\n{system}\n\n[USER]\n{prompt}",
        response=raw,
    )
    try:
        final = _extract_json(raw)
    except LLMServiceError as exc:
        _log_llm_parse_failure(
            stage="consolidator", output=raw, error=exc
        )
        return _deduplicate_comments(all_comments)

    normalized = []
    for c in final:
        sev = c.get("severity", "info")
        if sev not in ("info", "warning", "error"):
            sev = "info"
        normalized.append(_normalize_comment({**c, "severity": sev}))
    return normalized


# ── Internal orchestration pass ──────────────────────────────────────────────

async def _run_agentic_pipeline(
    *,
    llm: "ChatOpenAI",
    tools: list,
    diff_chunk: str,
    system_text: str,
    review_guidance: str,
    plan_context_tokens: int,
    max_context_tokens: int,
    graph_response_tokens: int,
    job_id: str | None = None,
    model_name: str = "",
) -> list[dict]:
    """
    One full orchestrator pass on *diff_chunk*:
    plan → (security ‖ quality) → consolidate.
    """
    # Orchestrator plans the review
    plan = await _plan_review(
        llm, diff_chunk, plan_context_tokens,
        job_id=job_id, model_name=model_name,
    )

    # Sub-agents run concurrently (each with its own ReAct loop)
    security_comments, quality_comments = await asyncio.gather(
        _security_agent(
            llm, tools, diff_chunk, plan, review_guidance, system_text,
            job_id=job_id, model_name=model_name,
        ),
        _quality_agent(
            llm, tools, diff_chunk, plan, review_guidance, system_text,
            job_id=job_id, model_name=model_name,
        ),
    )

    # Consolidate
    return await _consolidate(
        llm,
        security_comments + quality_comments,
        review_guidance,
        max_context_tokens,
        graph_response_tokens,
        job_id=job_id,
        model_name=model_name,
    )


# ── Public entry point ───────────────────────────────────────────────────────

async def run_review_graph(
    diff: str,
    metadata: dict,
    system_prompt: Prompt | None,
    review_prompt: Prompt,
    model: LLMModel,
    api_key: str | None,
    job_id: str | None = None,
    triggered_by_id: str | None = None,
    # Agentic context — enables tools for sub-agents
    gitlab_client: "GitLabClient | None" = None,
    project_id: int | None = None,
    git_ref: str | None = None,
) -> list[dict]:
    """
    Run the agentic review pipeline.

    When *gitlab_client*, *project_id*, and *git_ref* are provided, each
    sub-agent receives tools to fetch any repository file for deeper context.
    Without them the pipeline still works (diff-only analysis).

    Falls back to single-shot chain on unrecoverable errors.
    """
    from app.services.event_log_service import log_event

    llm = _build_llm(model, api_key)

    system_text = (
        _enforce_russian_output(system_prompt.content)
        if system_prompt
        else _enforce_russian_output(
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

    review_guidance = _render_review_prompt_instructions(
        review_prompt.content, metadata
    )

    # Build tools (empty list if no GitLab context)
    tools: list = []
    if gitlab_client and project_id and git_ref:
        from app.langchain_integration.agent_tools import make_gitlab_tools
        tools = make_gitlab_tools(gitlab_client, project_id, git_ref)

    # Chunk budget — smallest across all sub-agent prompt templates
    graph_chunk_budget = min(
        _available_prompt_tokens(
            max_context_tokens=model.max_context_tokens,
            fixed_texts=["plan system", "plan prompt"],
            response_tokens=graph_response_tokens,
        ),
        _available_prompt_tokens(
            max_context_tokens=model.max_context_tokens,
            fixed_texts=[system_text, review_guidance, "security task"],
            response_tokens=graph_response_tokens,
        ),
        _available_prompt_tokens(
            max_context_tokens=model.max_context_tokens,
            fixed_texts=[system_text, review_guidance, "quality task"],
            response_tokens=graph_response_tokens,
        ),
    )

    chunks = _chunk_diff(diff, graph_chunk_budget)
    all_comments: list[dict] = []
    _log_base = {
        "job_id": job_id,
        "model": model.model_name,
        "diff_chars": len(diff),
        "chunks": len(chunks),
        "agentic_tools": bool(tools),
    }

    await log_event(
        None,
        "llm.review_started",
        (
            f"Agentic review started — model '{model.model_name}', "
            f"{len(chunks)} chunk(s), "
            f"tools={'enabled' if tools else 'disabled'}"
        ),
        details=_log_base,
    )

    for chunk_idx, chunk in enumerate(chunks):
        try:
            await log_event(
                None,
                "llm.call",
                (
                    f"Agentic pipeline — chunk {chunk_idx + 1}/{len(chunks)}"
                    " (plan → security ‖ quality → consolidate)"
                ),
                details={
                    **_log_base,
                    "chunk": chunk_idx + 1,
                    "chunk_chars": len(chunk),
                },
            )
            comments = await _run_agentic_pipeline(
                llm=llm,
                tools=tools,
                diff_chunk=chunk,
                system_text=system_text,
                review_guidance=review_guidance,
                plan_context_tokens=plan_context_tokens,
                max_context_tokens=model.max_context_tokens,
                graph_response_tokens=graph_response_tokens,
                job_id=job_id,
                model_name=model.model_name,
            )
            await log_event(
                None,
                "llm.response",
                (
                    f"Agentic chunk {chunk_idx + 1}/{len(chunks)} "
                    f"complete — {len(comments)} comment(s)"
                ),
                details={
                    **_log_base,
                    "chunk": chunk_idx + 1,
                    "comments": len(comments),
                },
            )
            all_comments.extend(comments)

        except Exception as exc:
            logger.warning(
                "Agentic review failed on chunk %d, falling back: %s",
                chunk_idx + 1,
                exc,
            )
            await log_event(
                None,
                "llm.fallback",
                (
                    f"Agentic review failed on chunk {chunk_idx + 1}, "
                    f"using single-shot fallback: {exc}"
                ),
                level="warning",
                details={
                    **_log_base,
                    "chunk": chunk_idx + 1,
                    "error": str(exc),
                },
            )
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

    await log_event(
        None,
        "llm.review_completed",
        f"Agentic review done — {len(all_comments)} total comment(s)",
        details={**_log_base, "total_comments": len(all_comments)},
    )
    return all_comments
