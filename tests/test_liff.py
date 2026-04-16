"""Tests for app/routers/liff.py — Pydantic model validation."""

import pytest
from pydantic import ValidationError

from app.routers.liff import CheckInRequest


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
