import asyncio
import uuid

from app.workers import tasks


def teardown_function(_function=None):
    tasks._close_runner()


def test_run_review_job_reuses_same_event_loop(monkeypatch):
    loop_ids = []

    async def fake_execute_review(job_id):
        assert isinstance(job_id, uuid.UUID)
        loop_ids.append(id(asyncio.get_running_loop()))
        return {"status": "completed"}

    monkeypatch.setattr(tasks, "_execute_review", fake_execute_review)

    first_result = tasks._run_review_job_sync(uuid.uuid4())
    second_result = tasks._run_review_job_sync(uuid.uuid4())

    assert first_result == {"status": "completed"}
    assert second_result == {"status": "completed"}
    assert len(loop_ids) == 2
    assert loop_ids[0] == loop_ids[1]
