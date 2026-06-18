"""Tests for the Suno Song Studio backend.

Run:  pip install pytest httpx  &&  pytest
LLM calls are monkeypatched, so these run offline and never hit Claude.
"""
import json

import pytest
from fastapi.testclient import TestClient

import server
from server import app, SunoRequest, _parse_json, _clamp_pct, _to_response, to_camelot

client = TestClient(app)

FAKE = {
    "title": "Test Song",
    "style": "smooth r&b, 85 bpm, female vocals",
    "lyrics": "[Verse]\nline one\n\n[Chorus]\nhook line",
    "exclude_styles": "edm, metal",
    "vocal_gender": "female",
    "weirdness": 30,
    "style_influence": 65,
    "musical_key": "C minor",
    "bpm": "85",
    "chords": "Verse: Cm - Ab - Eb - Bb",
}


def test_camelot():
    assert to_camelot("C minor") == "5A"
    assert to_camelot("A minor") == "8A"
    assert to_camelot("C major") == "8B"
    assert to_camelot("F# minor") == "11A"
    assert to_camelot("") == ""
    assert to_camelot("nonsense") == ""


def test_to_response_adds_camelot():
    r = _to_response(dict(FAKE), SunoRequest(idea="x"))
    assert r.camelot == "5A"
    assert r.bpm == "85"
    assert r.chords.startswith("Verse:")


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    """Replace both LLM backends with a deterministic stub."""
    monkeypatch.setattr(server, "API_KEY", "")  # force CLI path
    monkeypatch.setattr(server, "_call_cli", lambda msg, system=None: dict(FAKE))
    monkeypatch.setattr(server, "_call_api", lambda msg, system=None: dict(FAKE))


# ── pure helpers ──
def test_parse_json_plain():
    assert _parse_json('{"a":1}') == {"a": 1}


def test_parse_json_fenced():
    assert _parse_json('```json\n{"a":1}\n```') == {"a": 1}


def test_parse_json_embedded():
    assert _parse_json('here you go {"a":1} thanks') == {"a": 1}


def test_clamp_pct():
    assert _clamp_pct(150, 50) == 100
    assert _clamp_pct(-5, 50) == 0
    assert _clamp_pct("x", 42) == 42
    assert _clamp_pct(None, 7) == 7


def test_to_response_user_override_wins():
    req = SunoRequest(idea="x", vocal_gender="male", weirdness=10, style_influence=20)
    r = _to_response(dict(FAKE), req)
    assert r.vocal_gender == "male"      # overrides FAKE's "female"
    assert r.weirdness == 10
    assert r.style_influence == 20


def test_to_response_falls_back_to_llm():
    req = SunoRequest(idea="x")
    r = _to_response(dict(FAKE), req)
    assert r.vocal_gender == "female"
    assert r.weirdness == 30
    assert r.style_influence == 65


# ── endpoints ──
def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_suno_empty_idea_400():
    assert client.post("/api/suno", json={"idea": "  "}).status_code == 400


def test_suno_ok():
    r = client.post("/api/suno", json={"idea": "late night drive"})
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == "Test Song"
    assert d["vocal_gender"] == "female"


def test_complete_requires_seed():
    assert client.post("/api/suno/complete", json={"seed_lyrics": ""}).status_code == 400


def test_complete_ok():
    r = client.post("/api/suno/complete", json={"seed_lyrics": "my line"})
    assert r.status_code == 200
    assert r.json()["lyrics"]


def test_rework_requires_fields():
    assert client.post("/api/suno/rework", json={"lyrics": "", "section": ""}).status_code == 400


def test_batch_count():
    r = client.post("/api/suno/batch", json={"idea": "x", "count": 3})
    assert r.status_code == 200
    assert len(r.json()["songs"]) == 3
