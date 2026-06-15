from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import job_concurrency


@pytest.fixture
def settings():
    return SimpleNamespace(
        max_concurrent_jobs=10,
        max_concurrent_ingest=10,
        max_concurrent_insights=5,
        max_concurrent_ingest_per_agent=3,
        max_concurrent_insights_per_agent=2,
    )


@contextmanager
def _fake_admission_lock(*args, **kwargs):
    yield True


def test_can_start_ingest_when_empty(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    db = MagicMock()
    db.scalar.side_effect = [0, 0]
    assert job_concurrency.can_start_job(db, "ingest") is True


def test_cannot_start_ingest_when_total_full(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    db = MagicMock()
    db.scalar.side_effect = [5, 5]
    assert job_concurrency.can_start_job(db, "ingest") is False


def test_cannot_start_ingest_when_insights_fill_global(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    db = MagicMock()
    db.scalar.side_effect = [4, 6]  # total 10 == global cap
    assert job_concurrency.can_start_job(db, "ingest") is False


def test_can_start_insights_until_limit(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    db = MagicMock()
    db.scalar.side_effect = [2, 4]  # insights 4 < 5, total 6 < 10
    assert job_concurrency.can_start_job(db, "insights") is True


def test_per_agent_ingest_limit_blocks(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    db = MagicMock()
    # global ingest 4 (< 10), insights 0, then this agent already has 3 (>= per-agent 3).
    db.scalar.side_effect = [4, 0, 3]
    assert job_concurrency.can_start_job(db, "ingest", "agent-1") is False


def test_per_agent_ingest_under_limit_allows(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    db = MagicMock()
    db.scalar.side_effect = [4, 0, 2]  # this agent has 2 (< per-agent 3)
    assert job_concurrency.can_start_job(db, "ingest", "agent-1") is True


def test_per_agent_insights_limit_blocks(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    db = MagicMock()
    # insights global 3 (< 5), total 3, then this agent already has 2 (>= per-agent 2).
    db.scalar.side_effect = [0, 3, 2]
    assert job_concurrency.can_start_job(db, "insights", "agent-1") is False


def test_admit_and_create_no_wait_raises_when_full(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    monkeypatch.setattr(job_concurrency, "job_admission_lock", _fake_admission_lock)
    db = MagicMock()
    db.scalar.return_value = 10  # every count is at/over the cap -> assert raises
    with pytest.raises(job_concurrency.JobConcurrencyLimitError):
        job_concurrency.admit_and_create(db, "ingest", "a", lambda: "job", wait_for_slot=False)


def test_admit_and_create_no_wait_persists_when_free(monkeypatch, settings):
    monkeypatch.setattr(job_concurrency, "get_settings", lambda: settings)
    monkeypatch.setattr(job_concurrency, "job_admission_lock", _fake_admission_lock)
    db = MagicMock()
    db.scalar.side_effect = [0, 0, 0]  # global + per-agent all free
    result = job_concurrency.admit_and_create(db, "ingest", "a", lambda: "job", wait_for_slot=False)
    assert result == "job"


def test_fail_orphaned_jobs_marks_running_failed():
    db = MagicMock()
    ingest_job = SimpleNamespace(agent_id="a", status="running", error=None, completed_at=None)
    insights_job = SimpleNamespace(agent_id="a", status="running", error=None, completed_at=None)
    db.scalars.return_value.all.side_effect = [[ingest_job], [insights_job]]

    cleared = job_concurrency.fail_orphaned_jobs(db, "a")

    assert cleared == 2
    assert ingest_job.status == "failed"
    assert insights_job.status == "failed"
    assert ingest_job.completed_at is not None
    assert db.commit.called
