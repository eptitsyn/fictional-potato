from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import review_service


def test_build_note_discussion_index_maps_note_ids_to_discussions():
    discussions = [
        {
            "id": "discussion-1",
            "notes": [{"id": 101}, {"id": "102"}],
        },
        {
            "id": "discussion-2",
            "notes": [{"id": 201}, {"body": "missing id"}],
        },
        {
            "notes": [{"id": 999}],
        },
    ]

    assert review_service._build_note_discussion_index(discussions) == {
        "101": "discussion-1",
        "102": "discussion-1",
        "201": "discussion-2",
    }


@pytest.mark.asyncio
async def test_delete_gitlab_comments_for_job_deletes_commit_notes(monkeypatch):
    deleted = []

    class FakeGitLabClient:
        def __init__(self, base_url, token):
            assert base_url == "https://gitlab.example.com"
            assert token == "decrypted-token"

        async def list_commit_discussions(self, project_id, sha):
            assert project_id == 42
            assert sha == "abc123"
            return [
                {"id": "discussion-a", "notes": [{"id": 11}]},
                {"id": "discussion-b", "notes": [{"id": 22}]},
            ]

        async def delete_commit_discussion_note(self, project_id, sha, discussion_id, note_id):
            deleted.append((project_id, sha, discussion_id, note_id))

    monkeypatch.setattr(review_service, "GitLabClient", FakeGitLabClient)
    monkeypatch.setattr(
        review_service,
        "get_decrypted_access_token",
        lambda git_server: "decrypted-token",
    )

    job = SimpleNamespace(
        trigger_type="commit",
        commit_sha="abc123",
        mr_iid=None,
        comments=[
            SimpleNamespace(gitlab_note_id="11"),
            SimpleNamespace(gitlab_note_id="22"),
            SimpleNamespace(gitlab_note_id=None),
        ],
        repository=SimpleNamespace(
            gitlab_project_id=42,
            git_server=SimpleNamespace(base_url="https://gitlab.example.com"),
        ),
    )

    await review_service._delete_gitlab_comments_for_job(job)

    assert deleted == [
        (42, "abc123", "discussion-a", "11"),
        (42, "abc123", "discussion-b", "22"),
    ]


@pytest.mark.asyncio
async def test_delete_gitlab_comments_for_job_raises_when_gitlab_delete_fails(monkeypatch):
    class FakeGitLabClient:
        def __init__(self, base_url, token):
            pass

        async def list_mr_discussions(self, project_id, mr_iid):
            return [{"id": "discussion-a", "notes": [{"id": 55}]}]

        async def delete_mr_discussion_note(self, project_id, mr_iid, discussion_id, note_id):
            raise RuntimeError("boom")

    monkeypatch.setattr(review_service, "GitLabClient", FakeGitLabClient)
    monkeypatch.setattr(
        review_service,
        "get_decrypted_access_token",
        lambda git_server: "decrypted-token",
    )

    job = SimpleNamespace(
        trigger_type="mr",
        commit_sha=None,
        mr_iid=7,
        comments=[SimpleNamespace(gitlab_note_id="55")],
        repository=SimpleNamespace(
            gitlab_project_id=42,
            git_server=SimpleNamespace(base_url="https://gitlab.example.com"),
        ),
    )

    with pytest.raises(HTTPException, match="Failed to delete some GitLab comments"):
        await review_service._delete_gitlab_comments_for_job(job)
