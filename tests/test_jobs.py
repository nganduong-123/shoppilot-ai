from __future__ import annotations

from app.repository import repository


def test_job_queue_is_durable_and_deduplicates_provider_events():
    payload = {"shop_id": 1, "event": {"text": "hello"}}

    first = repository.enqueue_job(
        "meta_message", payload, dedupe_key="meta:mid-1"
    )
    duplicate = repository.enqueue_job(
        "meta_message", payload, dedupe_key="meta:mid-1"
    )
    job = repository.claim_job()

    assert first is True
    assert duplicate is False
    assert job["kind"] == "meta_message"
    assert job["payload"] == payload
    assert job["attempts"] == 1
    assert repository.job_stats()["running"] == 1

    repository.complete_job(job["id"])
    assert repository.job_stats() == {"queued": 0, "running": 0, "failed": 0}


def test_failed_job_retries_then_moves_to_dead_letter_state():
    repository.enqueue_job("unsupported", {}, dedupe_key="job:failure", max_attempts=1)
    job = repository.claim_job()

    repository.fail_job(job["id"], "provider unavailable", 1, 1)

    assert repository.job_stats()["failed"] == 1
