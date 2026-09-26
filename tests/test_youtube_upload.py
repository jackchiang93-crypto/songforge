from pathlib import Path

import pytest

from scripts.youtube_upload import parse_metadata


def test_parse_metadata(tmp_path: Path):
    path = tmp_path / "metadata.txt"
    path.write_text("TITLE\nNight Drive | R&B\n\nDESCRIPTION\nRainy-night playlist.\nSecond line.\n\nTAGS\nr&b, original music, 85 bpm", encoding="utf-8")
    assert parse_metadata(path) == {
        "title": "Night Drive | R&B",
        "description": "Rainy-night playlist.\nSecond line.",
        "tags": ["r&b", "original music", "85 bpm"],
    }


def test_parse_metadata_rejects_missing_description(tmp_path: Path):
    path = tmp_path / "metadata.txt"
    path.write_text("TITLE\nSong\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DESCRIPTION"):
        parse_metadata(path)
