from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_request_id_and_prometheus_metrics_are_exposed():
    with TestClient(app) as client:
        health = client.get("/api/health", headers={"X-Request-ID": "test-request-123"})
        metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.headers["X-Request-ID"] == "test-request-123"
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in health.headers["Content-Security-Policy"]
    assert metrics.status_code == 200
    assert "shoppilot_http_requests_total" in metrics.text
    assert 'route="/api/health"' in metrics.text
    assert "shoppilot_jobs" in metrics.text


def test_liveness_and_readiness_are_separate():
    with TestClient(app) as client:
        live = client.get("/api/health/live")
        ready = client.get("/api/health/ready")

    assert live.json()["status"] == "ok"
    assert ready.json()["ready"] is True
    assert "storage" in ready.json()


def test_embeddable_widget_is_the_only_page_allowed_in_an_iframe():
    with TestClient(app) as client:
        widget = client.get("/static/widget.html")
        dashboard = client.get("/")

    assert widget.status_code == 200
    assert "X-Frame-Options" not in widget.headers
    assert "frame-ancestors *" in widget.headers["Content-Security-Policy"]
    assert dashboard.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in dashboard.headers["Content-Security-Policy"]


def test_unsafe_request_id_is_replaced_before_writing_response_headers():
    with TestClient(app) as client:
        response = client.get("/api/health/live", headers={"X-Request-ID": "invalid/request/id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "invalid/request/id"
    assert len(response.headers["X-Request-ID"]) == 36
