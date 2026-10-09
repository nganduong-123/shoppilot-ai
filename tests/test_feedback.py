from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_human_feedback_is_persisted_and_aggregated():
    with TestClient(app) as client:
        chat = client.post(
            "/api/shops/mint-fashion/chat", json={"message": "Tìm áo sơ mi trắng"}
        ).json()
        feedback = client.post(
            f"/api/conversations/{chat['conversation_id']}/feedback",
            json={"label": "helpful", "note": "Đúng catalog và giá."},
        )
        metrics = client.get("/api/shops/mint-fashion/metrics").json()

    assert feedback.status_code == 201
    assert feedback.json()["label"] == "helpful"
    assert metrics["feedback_total"] == 1
    assert metrics["feedback_helpful_rate"] == 100.0
