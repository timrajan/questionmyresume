import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.rate_limit import RateLimiter, client_ip


def test_allows_up_to_limit_then_blocks():
    limiter = RateLimiter(3, 3600, "things")
    for _ in range(3):
        limiter.check("1.2.3.4")
    with pytest.raises(HTTPException) as exc:
        limiter.check("1.2.3.4")
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "rate_limited"
    assert "3 things per hour" in exc.value.detail["message"]
    assert int(exc.value.headers["Retry-After"]) > 0


def test_limits_are_per_ip():
    limiter = RateLimiter(1, 3600, "things")
    limiter.check("1.1.1.1")
    limiter.check("2.2.2.2")  # different IP, own budget


def test_window_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("app.rate_limit.time.monotonic", lambda: now[0])
    limiter = RateLimiter(1, 60, "things")
    limiter.check("ip")
    now[0] += 61
    limiter.check("ip")  # old hit has aged out


def test_zero_disables():
    limiter = RateLimiter(0, 60, "things")
    for _ in range(100):
        limiter.check("ip")


class _Req:
    def __init__(self, headers, host="10.0.0.1"):
        self.headers = headers
        self.client = type("C", (), {"host": host})()


def test_client_ip_prefers_proxy_real_ip():
    headers = {"x-real-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.9, 84.17.44.226"}
    assert client_ip(_Req(headers)) == "203.0.113.9"
    assert client_ip(_Req({})) == "10.0.0.1"


def test_validate_endpoint_returns_429(monkeypatch):
    monkeypatch.setattr(main, "validate_limiter", RateLimiter(2, 3600, "job link checks"))
    client = TestClient(main.app)
    bad = {"url": "not a url"}  # fails fast, but still counts as an attempt
    assert client.post("/api/validate-job", json=bad).status_code == 422
    assert client.post("/api/validate-job", json=bad).status_code == 422
    resp = client.post("/api/validate-job", json=bad, headers={"X-Forwarded-For": "9.9.9.9"})
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "rate_limited"


def test_analyze_bad_file_does_not_use_quota(monkeypatch):
    limiter = RateLimiter(1, 3600, "resume analyses")
    monkeypatch.setattr(main, "analyze_limiter", limiter)
    client = TestClient(main.app)
    for _ in range(3):
        resp = client.post("/api/analyze", files={"resume": ("r.png", b"x")}, data={"job_id": "x"})
        assert resp.status_code == 415
    assert not limiter._hits
