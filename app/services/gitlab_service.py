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
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            r = await client.get(url, headers=self._headers, params=params)
        if not r.is_success:
            raise GitLabServiceError(
                f"GitLab GET {path} failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()

    async def _get_with_response(
        self, path: str, params: dict | None = None
    ) -> tuple[dict | list, httpx.Response]:
        url = f"{self._base}/api/v4{path}"
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            r = await client.get(url, headers=self._headers, params=params)
        if not r.is_success:
            raise GitLabServiceError(
                f"GitLab GET {path} failed: {r.status_code} {r.text[:200]}"
            )
        return r.json(), r

    async def _post(self, path: str, json: dict) -> dict:
        url = f"{self._base}/api/v4{path}"
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            r = await client.post(url, headers=self._headers, json=json)
        if not r.is_success:
            raise GitLabServiceError(
                f"GitLab POST {path} failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()

    async def _delete(self, path: str, *, allow_missing: bool = False) -> None:
        url = f"{self._base}/api/v4{path}"
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            r = await client.delete(url, headers=self._headers)
        if r.status_code == 404 and allow_missing:
            return
        if not r.is_success:
            raise GitLabServiceError(
                f"GitLab DELETE {path} failed: {r.status_code} {r.text[:200]}"
            )

    async def _list_paginated(self, path: str, params: dict | None = None) -> list[dict]:
        page = 1
        items: list[dict] = []

        while True:
            payload, response = await self._get_with_response(
                path,
                params={**(params or {}), "per_page": 100, "page": page},
            )
            if not isinstance(payload, list):
                break

            items.extend(item for item in payload if isinstance(item, dict))
            next_page = response.headers.get("X-Next-Page")
            if not next_page:
                break
            page = int(next_page)

        return items

    # ── Commit ──────────────────────────────────────────────────────────────

    async def get_commit(self, project_id: int, sha: str) -> dict:
        return await self._get(f"/projects/{project_id}/repository/commits/{sha}")

    async def list_projects(self) -> list[dict]:
        projects: list[dict] = []
        page = 1

        while True:
            payload, response = await self._get_with_response(
                "/projects",
                params={
                    "membership": True,
                    "simple": True,
                    "archived": False,
                    "order_by": "path",
                    "sort": "asc",
                    "per_page": 100,
                    "page": page,
                },
            )
            if not isinstance(payload, list):
                break

            projects.extend(item for item in payload if isinstance(item, dict))
            next_page = response.headers.get("X-Next-Page")
            if not next_page:
                break
            page = int(next_page)

        return projects

    async def get_commit_diff(self, project_id: int, sha: str) -> str:
        return _format_diff(await self.get_commit_diff_entries(project_id, sha))

    async def get_commit_diff_entries(self, project_id: int, sha: str) -> list[dict]:
        data = await self._get(f"/projects/{project_id}/repository/commits/{sha}/diff")
        return data if isinstance(data, list) else []

    async def list_commit_discussions(self, project_id: int, sha: str) -> list[dict]:
        return await self._list_paginated(
            f"/projects/{project_id}/repository/commits/{sha}/discussions"
        )

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

    async def post_commit_discussion(
        self,
        project_id: int,
        sha: str,
        body: str,
        position: dict | None = None,
    ) -> dict:
        payload: dict = {"body": body}
        if position:
            payload["position"] = position
        return await self._post(
            f"/projects/{project_id}/repository/commits/{sha}/discussions",
            payload,
        )

    async def delete_commit_discussion_note(
        self,
        project_id: int,
        sha: str,
        discussion_id: str,
        note_id: str,
    ) -> None:
        await self._delete(
            f"/projects/{project_id}/repository/commits/{sha}/discussions/"
            f"{discussion_id}/notes/{note_id}",
            allow_missing=True,
        )

    # ── Merge Request ───────────────────────────────────────────────────────

    async def get_mr(self, project_id: int, mr_iid: int) -> dict:
        return await self._get(f"/projects/{project_id}/merge_requests/{mr_iid}")

    async def get_mr_changes(self, project_id: int, mr_iid: int) -> str:
        data = await self.get_mr_changes_payload(project_id, mr_iid)
        return _format_diff(data.get("changes", []))

    async def get_mr_changes_payload(self, project_id: int, mr_iid: int) -> dict:
        data = await self._get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/changes"
        )
        return data if isinstance(data, dict) else {}

    async def list_mr_discussions(self, project_id: int, mr_iid: int) -> list[dict]:
        return await self._list_paginated(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions"
        )

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

    async def delete_mr_discussion_note(
        self,
        project_id: int,
        mr_iid: int,
        discussion_id: str,
        note_id: str,
    ) -> None:
        await self._delete(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions/"
            f"{discussion_id}/notes/{note_id}",
            allow_missing=True,
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
