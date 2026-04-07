import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import review_service


class FakeDB:
    def __init__(self):
        self.commit_calls = 0
        self.refresh_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, _obj):
        self.refresh_calls += 1


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


@pytest.mark.asyncio
async def test_enqueue_job_marks_job_failed_when_publish_fails(monkeypatch):
    db = FakeDB()
    job = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        error_message=None,
        completed_at=None,
    )

    class BrokenTask:
        @staticmethod
        def apply_async(*, args, queue):
            assert args == (str(job.id),)
            assert queue == "reviews"
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(review_service, "run_review_job", BrokenTask())

    with pytest.raises(HTTPException) as exc_info:
        await review_service._enqueue_job(db, job)

    assert exc_info.value.status_code == 503
    assert job.status == "failed"
    assert job.completed_at is not None
    assert "Failed to enqueue review job: broker unavailable" == job.error_message
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_requeue_pending_job_rejects_recent_jobs(monkeypatch):
    db = FakeDB()
    job = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        started_at=None,
        created_at=datetime.now(UTC) - timedelta(seconds=5),
        error_message=None,
        completed_at=None,
    )

    async def fake_get_job(_db, _job_id):
        return job

    monkeypatch.setattr(review_service, "get_job", fake_get_job)

    with pytest.raises(HTTPException) as exc_info:
        await review_service.requeue_pending_job(db, job.id)

    assert exc_info.value.status_code == 409
    assert "older than 30 seconds" in exc_info.value.detail


@pytest.mark.asyncio
async def test_requeue_pending_job_enqueues_old_pending_jobs(monkeypatch):
    db = FakeDB()
    job = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        started_at=None,
        created_at=datetime.now(UTC) - timedelta(minutes=5),
        error_message="old error",
        completed_at=datetime.now(UTC) - timedelta(minutes=4),
    )
    enqueued = []

    async def fake_get_job(_db, _job_id):
        return job

    async def fake_enqueue_job(_db, _job):
        enqueued.append(_job.id)
        return _job

    monkeypatch.setattr(review_service, "get_job", fake_get_job)
    monkeypatch.setattr(review_service, "_enqueue_job", fake_enqueue_job)

    result = await review_service.requeue_pending_job(db, job.id)

    assert result is job
    assert enqueued == [job.id]
    assert job.error_message is None
    assert job.completed_at is None
    assert db.commit_calls == 1
    assert db.refresh_calls == 1
