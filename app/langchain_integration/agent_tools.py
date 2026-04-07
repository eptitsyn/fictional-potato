"""
GitLab-backed tools for code review agents.

Each tool is created via a factory so the GitLabClient, project_id,
and git ref are injected at runtime without global state.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from app.services.gitlab_service import GitLabClient

logger = logging.getLogger(__name__)

_MAX_FILE_CHARS = 12_000  # ~3k tokens — guard against huge files
_MAX_TREE_ENTRIES = 200


def make_gitlab_tools(
    gitlab_client: "GitLabClient",
    project_id: int,
    git_ref: str,
) -> list:
    """
    Return a list of LangChain tools backed by *gitlab_client*.

    Tools available to every review agent:
    - get_file_content  — fetch full source of any file at *git_ref*
    - list_directory    — list files/dirs at a repository path
    """

    @tool
    async def get_file_content(file_path: str) -> str:
        """
        Fetch the full source code of a file from the repository.

        Use this when you need context beyond the diff: to inspect imports,
        base classes, called functions, config files, related modules, etc.

        Args:
            file_path: Repository-relative path, e.g. 'app/services/auth.py'
        """
        try:
            content = await gitlab_client.get_file_content(project_id, file_path, git_ref)
        except Exception as exc:
            logger.warning("get_file_content failed for %s@%s: %s", file_path, git_ref, exc)
            return f"[Ошибка: не удалось получить файл '{file_path}': {exc}]"

        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + "\n...[файл обрезан]"
        return content

    @tool
    async def list_directory(path: str = "") -> str:
        """
        List files and subdirectories at a given path in the repository.

        Use this to explore project structure when you're unsure where
        related modules or config files live.

        Args:
            path: Directory path relative to repo root. Empty string = root.
        """
        try:
            entries = await gitlab_client.list_repository_tree(project_id, path, git_ref)
        except Exception as exc:
            logger.warning("list_directory failed for path=%s: %s", path, exc)
            return f"[Ошибка: не удалось получить список файлов в '{path}': {exc}]"

        lines = []
        for entry in entries[:_MAX_TREE_ENTRIES]:
            kind = "📁" if entry.get("type") == "tree" else "📄"
            lines.append(f"{kind} {entry.get('path', entry.get('name', '?'))}")
        if len(entries) > _MAX_TREE_ENTRIES:
            lines.append(f"... и ещё {len(entries) - _MAX_TREE_ENTRIES} элементов")
        return "\n".join(lines) if lines else "(директория пуста)"

    return [get_file_content, list_directory]
