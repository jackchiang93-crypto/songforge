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


def test_suno_advanced_settings_are_preserved():
    req = SunoRequest(
        idea="night drive", duration_mode="custom", duration_seconds=180,
        max_mode=True, variety="high", personalize=True,
        audio_reference="my piano sketch", voice_reference="warm alto",
        inspo_reference="rainy city",
    )
    r = _to_response(dict(FAKE), req)
    assert (r.duration_mode, r.duration_seconds, r.max_mode, r.variety, r.personalize) == (
        "custom", 180, True, "high", True,
    )
    assert r.audio_reference == "my piano sketch"
    assert r.voice_reference == "warm alto"
    assert r.inspo_reference == "rainy city"


def test_quality_warnings_do_not_block_song():
    req = SunoRequest(idea="night drive", duration_mode="custom")
    r = _to_response(dict(FAKE), req)
    assert r.title == "Test Song"
    assert any("Custom" in warning for warning in r.quality_warnings)


def test_quality_warns_on_conflicting_style_settings():
    req = SunoRequest(idea="night drive", vocal_gender="female", tempo="90 bpm")
    data = dict(FAKE, style="male vocals, warm rhodes, 85 bpm")
    warnings = _to_response(data, req).quality_warnings
    assert any("Vocal Gender" in warning for warning in warnings)
    assert any("BPM" in warning for warning in warnings)


def test_batch_preserves_advanced_controls():
    r = client.post("/api/suno/batch", json={
        "idea": "late night", "count": 2, "duration_mode": "custom",
        "duration_seconds": 150, "variety": "low", "weirdness": 17,
    })
    assert r.status_code == 200
    assert all(song["duration_seconds"] == 150 and song["weirdness"] == 17 for song in r.json()["songs"])


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


def test_codex_provider_routes_to_codex(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_call_codex", lambda msg, system=None: (calls.append(msg), dict(FAKE))[1])
    r = client.post("/api/suno", json={"idea": "rainy city", "provider": "codex"})
    assert r.status_code == 200
    assert len(calls) == 1 and "rainy city" in calls[0]


def test_bilingual_request_instructs_both_languages():
    msg = server._build_user_msg(SunoRequest(idea="late night", language="中文+English"))
    assert "ONE mixed-language song" in msg
    assert "中文" in msg and "English" in msg
    assert "Do not translate each line twice" in msg


def test_bilingual_quality_warning_for_missing_language():
    lyrics = "[Verse]\n這是中文歌詞\n\n[Chorus]\n只有中文副歌"
    r = _to_response(dict(FAKE, lyrics=lyrics), SunoRequest(idea="x", language="中文+English"))
    assert any("未見英文" in warning for warning in r.quality_warnings)


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


def test_project_plan_supports_custom_style_and_multiple_languages(monkeypatch):
    captured = {}
    def fake_plan(message, system=None):
        captured["message"] = message
        return {"songs": [
            {"concept": f"story {i}", "scene": f"place {i}", "emotional_turn": f"turn {i}",
             "hook_idea": f"hook {i}", "arrangement": f"instrument {i}",
             "language": ["中文", "日本語"][i % 2]}
            for i in range(10)
        ]}
    monkeypatch.setattr(server, "_call_cli", fake_plan)
    r = client.post("/api/suno/plan", json={
        "direction": "night journey", "style": "和風 city pop",
        "languages": ["中文", "日本語"], "count": 10,
    })
    assert r.status_code == 200
    assert len(r.json()["songs"]) == 10
    assert "和風 city pop" in captured["message"]


def test_project_plan_rejects_duplicate_hooks(monkeypatch):
    monkeypatch.setattr(server, "_call_cli", lambda message, system=None: {"songs": [
        {"concept": f"story {i}", "scene": f"place {i}", "emotional_turn": f"turn {i}",
         "hook_idea": "same hook", "arrangement": f"instrument {i}", "language": "English"}
        for i in range(2)
    ]})
    r = client.post("/api/suno/plan", json={
        "direction": "night journey", "style": "jazz", "languages": ["English"], "count": 2,
    })
    assert r.status_code == 502
