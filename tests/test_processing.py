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
    assert report == {"pages": 2, "layers": {0: "word", 1: "word"}, "withheld": []}
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
    assert report == {"pages": 1, "layers": {0: "line"}, "withheld": []}
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
    assert P.build_text([2, 0, 1], {0: "a", 1: "b", 2: "c"}, removed=[0]) == "--- Page 1 ---\nc\n\n--- Page 2 ---\nb\n"


# -- Editing and document tools ---------------------------------------------

def typed_pdf(pages=("Invoice 4471 for Asha Traders", "Second page total 9,250")) -> bytes:
    doc = fitz.open()
    for text in pages:
        doc.new_page(width=595, height=842).insert_text((72, 120), text, fontsize=14)
    return doc.tobytes()


def page_text(pdf: bytes, page_no: int = 0, password: str = "") -> str:
    doc = fitz.open(stream=pdf, filetype="pdf")
    if password:
        assert doc.authenticate(password)
    return doc[page_no].get_text()


def test_text_edit_lands_where_it_was_drawn_even_on_a_turned_page():
    for rotation in (0, 90, 180, 270):
        edit = {"kind": "text", "rect": [100, 300, 400, 340], "rotation": rotation, "text": "Approved", "size": 14,
                "color": "#1f3fbf"}
        pdf, _ = P.build_pdf(typed_pdf(), {0: rotation}, edits={0: [edit]})
        page = fitz.open(stream=pdf, filetype="pdf")[0]
        box = next(fitz.Rect(w[:4]) * page.rotation_matrix for w in page.get_text("words") if w[4] == "Approved")
        box.normalize()
        assert fitz.Rect(98, 295, 402, 345).contains(box), (rotation, box)


def test_redaction_removes_the_text_itself():
    source = typed_pdf()
    hit = P.find_text(source, 0, "Asha Traders")[0]
    pdf, _ = P.build_pdf(source, edits={0: [{"kind": "redact", "rect": hit["rect"], "rotation": 0}]})
    text = page_text(pdf)
    assert "Asha" not in text and "Invoice 4471" in text


def test_replace_swaps_text_in_place():
    source = typed_pdf()
    hit = P.find_text(source, 0, "4471")[0]
    edit = {"kind": "replace", "rotation": 0, "text": "5582", **hit}
    pdf, _ = P.build_pdf(source, edits={0: [edit]})
    text = page_text(pdf)
    assert "5582" in text and "4471" not in text and "Asha Traders" in text


def test_cover_highlight_and_image_edits_apply():
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (120, 40), "navy").save(buf, "PNG")
    edits = [
        {"kind": "cover", "rect": [60, 100, 300, 130], "rotation": 0, "color": "#ffffff"},
        {"kind": "highlight", "rect": [60, 200, 300, 230], "rotation": 0, "color": "#ffd60a"},
        {"kind": "image", "rect": [60, 400, 180, 440], "rotation": 0, "data": buf.getvalue()},
    ]
    edited = P.render_page(typed_pdf(), 0, 72, edits=edits)
    assert edited.crop((60, 100, 300, 130)).getcolors() == [(240 * 30, (255, 255, 255))]  # text covered
    r, g, b = edited.getpixel((150, 215))
    assert r > 200 and g > 200 and b < 200  # yellow wash
    assert edited.getpixel((120, 420)) == (0, 0, 128)
    pdf, _ = P.build_pdf(typed_pdf(), edits={0: edits})
    assert len(fitz.open(stream=pdf, filetype="pdf")[0].get_images()) == 1


@needs_ocr
def test_find_and_redact_on_a_scan(results):
    hits = P.find_text(SAMPLE, 0, "sahyog housing", 0, results[0])
    assert len(hits) == 2 and all(h["fill"] != h["color"] for h in hits)
    edits = {0: [{"kind": "redact", "rect": hits[0]["rect"], "rotation": 0}]}
    pdf, report = P.build_pdf(SAMPLE, {1: 90}, results=results, text_layer=True, edits=edits)
    assert report["withheld"] == [0] and report["layers"] == {1: "word"}
    assert page_text(pdf).strip() == ""  # no hidden text left behind a redaction
    box = fitz.Rect(hits[0]["rect"])
    shot = P.render_page(pdf, 0, 72).crop(tuple(int(v) for v in (box.x0 + 3, box.y0 + 3, box.x1 - 3, box.y1 - 3)))
    assert max(shot.convert("L").getdata()) < 40  # blacked out in the image itself


def test_merge_reorder_and_remove():
    merged, added = P.merge_pdfs(typed_pdf(), typed_pdf(("Appendix A",)))
    assert added == 1 and P.inspect_pdf(merged) == 3
    pdf, report = P.build_pdf(merged, order=[2, 0, 1], removed=[1])
    assert report["pages"] == 2
    assert "Appendix A" in page_text(pdf, 0) and "Invoice" in page_text(pdf, 1)
    too_many = fitz.open()
    for _ in range(P.MAX_PAGES):
        too_many.new_page()
    with pytest.raises(P.PdfError):
        P.merge_pdfs(typed_pdf(), too_many.tobytes())


def test_password_round_trip():
    locked, _ = P.build_pdf(typed_pdf(), password="open-sesame")
    with pytest.raises(P.PdfLocked):
        P.inspect_pdf(locked)
    with pytest.raises(P.PdfLocked):
        P.unlock_pdf(locked, "wrong")
    assert "Invoice 4471" in page_text(P.unlock_pdf(locked, "open-sesame"))


def test_watermark_and_page_numbers():
    pdf, _ = P.build_pdf(typed_pdf(), {1: 90}, watermark="DRAFT", page_numbers=True)
    for page_no, number in ((0, "1 / 2"), (1, "2 / 2")):
        text = page_text(pdf, page_no)
        assert "DRAFT" in text and number in text
    page = fitz.open(stream=pdf, filetype="pdf")[1]
    box = next(fitz.Rect(w[:4]) * page.rotation_matrix for w in page.get_text("words") if w[4] == "2")
    box.normalize()
    assert box.y0 > page.rect.height - 40 and abs((box.x0 + box.x1) / 2 - page.rect.width / 2) < 30


def test_compression_and_zip_exports():
    plain, _ = P.build_pdf(SAMPLE)
    small, _ = P.build_pdf(SAMPLE, compress=True)
    assert len(small) < len(plain) * 0.9
    assert P.render_page(small, 0, 50).size == P.render_page(plain, 0, 50).size
    import io, zipfile
    assert zipfile.ZipFile(io.BytesIO(P.pages_as_images(plain, 50))).namelist() == ["page-01.png", "page-02.png"]
    assert zipfile.ZipFile(io.BytesIO(P.pages_as_pdfs(plain))).namelist() == ["page-01.pdf", "page-02.pdf"]
