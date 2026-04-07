"""
GitLab REST API v4 client (async, httpx-based).
All methods are stateless — pass base_url + token per call.
"""
from __future__ import annotations

import httpx

from app.core.exceptions import GitLabServiceError


class GitLabClient:
    def __init__(self, base_url: str, token: str):
        self._base = base_url.rstrip("/")
        self._headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{self._base}/api/v4{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self._headers, params=params)
        if not r.is_success:
            raise GitLabServiceError(
                f"GitLab GET {path} failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()

    async def _post(self, path: str, json: dict) -> dict:
        url = f"{self._base}/api/v4{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=self._headers, json=json)
        if not r.is_success:
            raise GitLabServiceError(
                f"GitLab POST {path} failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()

    # ── Commit ──────────────────────────────────────────────────────────────

    async def get_commit(self, project_id: int, sha: str) -> dict:
        return await self._get(f"/projects/{project_id}/repository/commits/{sha}")

    async def get_commit_diff(self, project_id: int, sha: str) -> str:
        data = await self._get(f"/projects/{project_id}/repository/commits/{sha}/diff")
        return _format_diff(data)

    async def post_commit_comment(
        self,
        project_id: int,
        sha: str,
        note: str,
        path: str | None = None,
        line: int | None = None,
        line_type: str = "new",
    ) -> dict:
        body: dict = {"note": note}
        if path:
            body["path"] = path
        if line is not None:
            body["line"] = line
            body["line_type"] = line_type
        return await self._post(f"/projects/{project_id}/repository/commits/{sha}/comments", body)

    # ── Merge Request ───────────────────────────────────────────────────────

    async def get_mr(self, project_id: int, mr_iid: int) -> dict:
        return await self._get(f"/projects/{project_id}/merge_requests/{mr_iid}")

    async def get_mr_changes(self, project_id: int, mr_iid: int) -> str:
        data = await self._get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/changes"
        )
        changes = data.get("changes", [])
        return _format_diff(changes)

    async def post_mr_note(self, project_id: int, mr_iid: int, body: str) -> dict:
        return await self._post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
            {"body": body},
        )

    async def post_mr_discussion(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
        position: dict | None = None,
    ) -> dict:
        payload: dict = {"body": body}
        if position:
            payload["position"] = position
        return await self._post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            payload,
        )


def _format_diff(changes: list[dict]) -> str:
    """Convert GitLab diff objects to unified diff text."""
    parts = []
    for change in changes:
        old_path = change.get("old_path", "")
        new_path = change.get("new_path", "")
        diff = change.get("diff", "")
        parts.append(f"--- {old_path}\n+++ {new_path}\n{diff}")
    return "\n".join(parts)
