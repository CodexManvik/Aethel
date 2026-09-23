import pytest
from pydantic import ValidationError

from aethel.runtime.checks import Check, run_checks


def test_checks(tmp_path):
    f = tmp_path / "essay.txt"
    f.write_text("Carbon pricing works when " + "word " * 20, encoding="utf-8")
    results = run_checks([
        Check(kind="file_exists", path=str(f)),
        Check(kind="file_exists", path=str(tmp_path / "missing.txt")),
        Check(kind="file_contains", path=str(f), text="carbon PRICING"),
        Check(kind="min_words", path=str(f), count=10),
        Check(kind="min_words", path=str(f), count=500),
    ])
    assert [r.passed for r in results] == [True, False, True, True, False]
    assert results[0].description == f"{f} exists"
    assert "24 words" in results[4].detail


def test_check_validation():
    with pytest.raises(ValidationError):
        Check(kind="file_contains", path="C:/a.txt")  # needs text
    with pytest.raises(ValidationError):
        Check(kind="min_words", path="C:/a.txt")      # needs count
    with pytest.raises(ValidationError):
        Check(kind="rm_rf", path="C:/")
