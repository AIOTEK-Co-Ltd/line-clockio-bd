"""Tests for app/routers/liff.py — page serving and Pydantic model validation."""

import pytest
from pydantic import ValidationError
from unittest.mock import patch

from app.routers.liff import CheckInRequest


# ── GET /liff/ page ───────────────────────────────────────────────────────────

def test_liff_page_returns_200(client):
    """LIFF page is served with HTTP 200."""
    resp = client.get("/liff/")
    assert resp.status_code == 200


def test_liff_page_is_html(client):
    """Response Content-Type is text/html."""
    resp = client.get("/liff/")
    assert "text/html" in resp.headers["content-type"]


def test_liff_page_contains_liff_id(client):
    """The stub LIFF ID from conftest is injected into the page."""
    resp = client.get("/liff/")
    assert "test-liff-id" in resp.text


def test_liff_page_contains_api_url(client):
    """The APP_BASE_URL is injected so the JS fetch target is correct."""
    resp = client.get("/liff/")
    assert "http://localhost:8000" in resp.text


def test_liff_page_contains_checkin_buttons(client):
    """Both clock-in and clock-out buttons are present."""
    resp = client.get("/liff/")
    assert "上班打卡" in resp.text
    assert "下班打卡" in resp.text


def test_liff_page_loads_liff_sdk(client):
    """LIFF SDK script tag is present."""
    resp = client.get("/liff/")
    assert "line-scdn.net/liff" in resp.text


# ── POST /liff/checkin — liff_enabled guard ───────────────────────────────────

def test_checkin_503_when_liff_not_configured(client):
    """POST /liff/checkin returns 503 when LIFF credentials are not set."""
    from app.config import Settings
    from unittest.mock import MagicMock

    disabled_settings = MagicMock(spec=Settings)
    disabled_settings.liff_enabled = False

    with patch("app.routers.liff.get_settings", return_value=disabled_settings):
        resp = client.post(
            "/liff/checkin",
            json={"type": "clock_in", "latitude": 25.0, "longitude": 121.0, "id_token": "tok"},
        )
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


# ── CheckInRequest field bounds ───────────────────────────────────────────────

def test_valid_clock_in():
    req = CheckInRequest(type="clock_in", latitude=25.033, longitude=121.565, id_token="tok")
    assert req.type == "clock_in"


def test_valid_clock_out_extreme_coords():
    req = CheckInRequest(type="clock_out", latitude=-90.0, longitude=180.0, id_token="tok")
    assert req.type == "clock_out"


def test_latitude_above_max_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=90.001, longitude=0.0, id_token="tok")


def test_latitude_below_min_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=-90.001, longitude=0.0, id_token="tok")


def test_longitude_above_max_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=0.0, longitude=180.001, id_token="tok")


def test_longitude_below_min_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=0.0, longitude=-180.001, id_token="tok")
