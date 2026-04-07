"""
LangChain chain construction for code review.
Uses ChatOpenAI with a configurable base_url so any OpenAI-compatible
endpoint (Ollama, Mistral, Azure, etc.) works transparently.
"""
from __future__ import annotations

import json
import re
import textwrap

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from app.core.exceptions import LLMServiceError
from app.models.llm import LLMModel
from app.models.prompt import Prompt

# Max chars to send per chunk (rough token-to-char ratio of ~4)
_CHARS_PER_TOKEN = 4
_SAFETY_FACTOR = 0.85  # leave headroom for prompt overhead


def _build_llm(model: LLMModel, api_key: str | None) -> ChatOpenAI:
    kwargs: dict = {
        "model": model.model_name,
        "temperature": model.temperature,
        "base_url": model.endpoint.base_url,
    }
    if api_key:
        kwargs["api_key"] = api_key
    else:
        # Some local endpoints ignore the key but langchain requires something
        kwargs["api_key"] = "no-key"
    return ChatOpenAI(**kwargs)


def _chunk_diff(diff: str, max_tokens: int) -> list[str]:
    """Split diff into chunks that fit within max_tokens."""
    max_chars = int(max_tokens * _CHARS_PER_TOKEN * _SAFETY_FACTOR)
    if len(diff) <= max_chars:
        return [diff]

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for block in diff.split("\n--- "):
        block_str = ("--- " + block) if chunks or current_chunk else block
        if current_len + len(block_str) > max_chars and current_chunk:
            chunks.append("\n--- ".join(current_chunk))
            current_chunk = [block]
            current_len = len(block_str)
        else:
            current_chunk.append(block)
            current_len += len(block_str)

    if current_chunk:
        chunks.append("\n--- ".join(current_chunk))

    return chunks


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

    Returns a list of dicts:
      {"file_path": str|None, "line_number": int|None, "severity": str, "comment": str}
    """
    llm = _build_llm(model, api_key)
    parser = StrOutputParser()

    system_text = system_prompt.content if system_prompt else textwrap.dedent("""
        You are an expert code reviewer. Respond ONLY with a valid JSON array.
        No markdown, no extra text, just the JSON array.
    """).strip()

    # Format the review prompt with metadata
    try:
        human_text = review_prompt.content.format(**metadata)
    except KeyError:
        human_text = review_prompt.content

    chunks = _chunk_diff(diff, model.max_context_tokens)
    all_comments: list[dict] = []

    for i, chunk in enumerate(chunks):
        chunk_human = human_text.replace("{diff}", chunk)
        if len(chunks) > 1:
            chunk_human += f"\n\n[This is chunk {i+1}/{len(chunks)} of the diff]"

        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=chunk_human),
        ]

        chain = llm | parser
        raw_output = await chain.ainvoke(messages)
        comments = _extract_json(raw_output)

        # Normalize and validate
        for c in comments:
            all_comments.append({
                "file_path": c.get("file_path"),
                "line_number": c.get("line_number"),
                "severity": c.get("severity", "info"),
                "comment_body": c.get("comment", c.get("comment_body", "")),
            })

    return all_comments
