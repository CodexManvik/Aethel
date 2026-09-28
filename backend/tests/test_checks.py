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


def test_word_counts_read_the_text_of_a_docx(tmp_path):
    import zipfile
    from aethel.runtime.checks import Check, run_check
    doc = tmp_path / "essay.docx"
    body = "".join(f"<w:p><w:r><w:t>Rain fa</w:t></w:r><w:r><w:t>lls softly {i}</w:t></w:r></w:p>" for i in range(5))
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    result = run_check(Check(kind="min_words", path=str(doc), count=20))
    assert result.passed and result.detail == "20 words"   # runs split mid-word don't inflate the count
    assert run_check(Check(kind="file_contains", path=str(doc), text="falls softly")).passed
