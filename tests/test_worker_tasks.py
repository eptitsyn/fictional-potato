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


def test_normalize_comment_lines_preserves_normalized_line_range():
    line_number, line_end = tasks._normalize_comment_lines(
        {
            "file_path": "app/example.py",
            "line_number": 12,
            "line_end": 15,
            "comment_body": "Validate the bounds before slicing.",
        }
    )

    assert line_number == 12
    assert line_end == 15


def test_normalize_comment_lines_reorders_normalized_line_range():
    line_number, line_end = tasks._normalize_comment_lines(
        {
            "file_path": "app/example.py",
            "line_number": 20,
            "line_end": 18,
            "comment_body": "The reported range should be ordered.",
        }
    )

    assert line_number == 18
    assert line_end == 20
