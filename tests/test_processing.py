"""End-to-end checks on the bundled sample scan. Needs Tesseract with eng, hin and osd."""
from pathlib import Path
import sys

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import processing as P  # noqa: E402

SAMPLE = (Path(__file__).resolve().parents[1] / "samples" / "sample-scan.pdf").read_bytes()
LANGS = set(P.tesseract_status()["langs"])
needs_ocr = pytest.mark.skipif(not {"eng", "hin", "osd"} <= LANGS, reason="Tesseract eng+hin+osd required")


@pytest.fixture(scope="module")
def results():
    found, errors = P.recognise_pages(SAMPLE, [0, 1], P.LANGUAGES["Hindi + English"], 300, auto_rotate=True)
    assert not errors
    return found


def test_inspect_rejects_bad_files():
    assert P.inspect_pdf(SAMPLE) == 2
    with pytest.raises(P.PdfError):
        P.inspect_pdf(b"not a pdf")
    with pytest.raises(P.PdfError):
        P.inspect_pdf(b"x" * (P.MAX_BYTES + 1))
    big = fitz.open()
    for _ in range(P.MAX_PAGES + 1):
        big.new_page()
    with pytest.raises(P.PdfError):
        P.inspect_pdf(big.tobytes())


@needs_ocr
def test_reads_both_scripts_and_keeps_digits(results):
    text = results[0]["text"]
    assert "annual general meeting" in text
    assert "सदस्यों" in text
    assert "SHS/2026/114" in text and "411038" in text  # the digit 1 must survive
    assert results[0]["confidence"] > 80


@needs_ocr
def test_sideways_page_is_turned_upright(results):
    assert results[0]["rotation"] == 0
    assert results[1]["rotation"] == 90
    assert "Maintenance statement" in results[1]["text"]


@needs_ocr
def test_text_layer_lines_up_with_the_scan(results):
    rotations = {p: r["rotation"] for p, r in results.items()}
    pdf, report = P.build_pdf(SAMPLE, rotations, results=results, text_layer=True)
    assert report == {"pages": 2, "layers": {0: "word", 1: "word"}}
    doc = fitz.open(stream=pdf, filetype="pdf")
    for page_no, result in results.items():
        page = doc[page_no]
        placed = {w[4]: fitz.Rect(w[:4]) * page.rotation_matrix for w in page.get_text("words")}
        texts = [w["text"] for w in result["words"]]
        checked = 0
        for word in result["words"]:
            if texts.count(word["text"]) != 1 or word["text"] not in placed or len(word["text"]) < 4:
                continue
            box = placed[word["text"]]
            assert abs(box.x0 - word["x0"]) < 6 and abs(box.x1 - word["x1"]) < 6
            assert abs((box.y0 + box.y1) / 2 - (word["y0"] + word["y1"]) / 2) < 6
            checked += 1
        assert checked > 20


@needs_ocr
def test_corrections_and_page_edits_reach_the_export(results):
    rotations = {p: r["rotation"] for p, r in results.items()}
    corrected = results[0]["text"].replace("Sahyog", "Sahayog")
    notes = [{"page": 0, "text": "Reviewed copy", "position": "Bottom right", "size": 12}]
    pdf, report = P.build_pdf(SAMPLE, rotations, removed=[1], notes=notes, results=results,
                              edited_text={0: corrected}, text_layer=True)
    assert report == {"pages": 1, "layers": {0: "line"}}
    text = fitz.open(stream=pdf, filetype="pdf")[0].get_text()
    assert "Sahayog" in text and "Reviewed copy" in text
    _, report = P.build_pdf(SAMPLE, rotations, results=results, edited_text={0: "one line only"}, text_layer=True)
    assert report["layers"][0] == "page"
    with pytest.raises(P.PdfError):
        P.build_pdf(SAMPLE, removed=[0, 1])


def test_pages_with_real_text_are_copied_not_recognised():
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "This page already has a real text layer in it.")
    result = P.recognise_page(doc.tobytes(), 0, "eng")
    assert result["source"] == "embedded" and "real text layer" in result["text"]


def test_text_export_skips_removed_pages():
    out = P.build_text(3, {0: "a", 1: "b", 2: " "}, removed=[1])
    assert out == "--- Page 1 ---\na\n"
