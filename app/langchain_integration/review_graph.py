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
from langgraph.graph import END, START, StateGraph

from app.core.exceptions import LLMServiceError
from app.langchain_integration.chains import _build_llm, _chunk_diff, _extract_json
from app.models.llm import LLMModel
from app.models.prompt import Prompt

logger = logging.getLogger(__name__)


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
        system_prompt.content
        if system_prompt
        else "You are an expert code reviewer. Respond ONLY with valid JSON. No prose outside JSON."
    )

    # Chunk diff if needed
    chunks = _chunk_diff(diff, model.max_context_tokens)
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
            comments = await _run_graph(llm, chain, parser, state, metadata, review_prompt)
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


async def _run_graph(llm, chain, parser, state: ReviewState, metadata: dict, review_prompt: Prompt) -> list[dict]:
    diff = state["diff"]
    system_text = state["system_prompt"]

    # ── Node 1: Planning ──────────────────────────────────────────────────────
    plan_prompt = f"""Analyze this diff and list:
1. Which files are changed and what they do
2. Top 3-5 areas to focus the review on (security, perf, logic, style, tests)
3. Any obvious red flags

Diff:
{diff}

Respond with a brief plain-text analysis (not JSON)."""

    plan = await chain.ainvoke([
        SystemMessage(content="You are a senior engineer planning a code review."),
        HumanMessage(content=plan_prompt),
    ])

    # ── Node 2: Security Review ────────────────────────────────────────────────
    security_prompt = f"""Review ONLY for security issues:
- Injection (SQL, command, XSS, path traversal)
- Auth/authz flaws, missing checks
- Secrets/credentials in code
- Insecure deserialization or crypto
- OWASP Top 10

Review plan context:
{plan}

Diff:
{diff}

Respond ONLY with a JSON array. Each item: {{"file_path": str|null, "line_number": int|null, "severity": "info"|"warning"|"error", "comment": str}}"""

    sec_raw = await chain.ainvoke([
        SystemMessage(content=system_text),
        HumanMessage(content=security_prompt),
    ])

    try:
        security_comments = _extract_json(sec_raw)
    except LLMServiceError:
        security_comments = []

    # ── Node 3: Quality Review ─────────────────────────────────────────────────
    quality_prompt = f"""Review for code quality, maintainability, and correctness:
- Bugs and logic errors
- Performance issues (N+1 queries, unnecessary loops, missing indexes)
- Missing error handling
- Dead code, unused variables
- Missing or inadequate tests
- API design issues

Review plan context:
{plan}

Diff:
{diff}

Respond ONLY with a JSON array. Each item: {{"file_path": str|null, "line_number": int|null, "severity": "info"|"warning"|"error", "comment": str}}"""

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

    consolidate_prompt = f"""You have collected these code review comments from multiple passes.
Remove exact duplicates, merge overlapping comments about the same line, and ensure severity is accurate.
Keep all unique, actionable feedback.

Comments to consolidate:
{json.dumps(all_raw, indent=2)}

Respond ONLY with the final JSON array. Each item: {{"file_path": str|null, "line_number": int|null, "severity": "info"|"warning"|"error", "comment": str}}"""

    final_raw = await chain.ainvoke([
        SystemMessage(content="You are an expert editor consolidating code review comments. Respond ONLY with JSON."),
        HumanMessage(content=consolidate_prompt),
    ])

    try:
        final = _extract_json(final_raw)
    except LLMServiceError:
        final = all_raw  # use un-consolidated if consolidation fails

    # Normalize
    normalized = []
    for c in final:
        sev = c.get("severity", "info")
        if sev not in ("info", "warning", "error"):
            sev = "info"
        normalized.append({
            "file_path": c.get("file_path"),
            "line_number": c.get("line_number"),
            "severity": sev,
            "comment_body": c.get("comment", c.get("comment_body", "")),
        })

    return normalized
