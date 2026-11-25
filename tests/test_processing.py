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


# -- Editor ----------------------------------------------------------------

def _typed_pdf(lines=("Invoice number 2291", "Payable by 14 December 2026", "Total due: Rs 48,500")) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    for i, text in enumerate(lines):
        page.insert_text((60, 80 + i * 14.5), text, fontsize=14, fontname="tiro")  # tight leading on purpose
    page.draw_rect(fitz.Rect(50, 60, 400, 130), color=(0, 0, 0))
    return doc.tobytes()


def _ink(pdf: bytes, box, page_no=0, rotation=0) -> int:
    """How many dark pixels a region of the rendered page holds."""
    image = P.render_page(pdf, page_no, 72, rotation).convert("L").crop(tuple(int(v) for v in box))
    return sum(1 for v in image.getdata() if v < 140)


def test_clicking_a_line_edits_only_that_line():
    pdf = _typed_pdf()
    line = P.text_line_at(pdf, 0, 100, 90)
    assert line["text"] == "Payable by 14 December 2026"
    assert (line["font"], line["erase"], line["size"]) == ("serif", "text", 14.0)
    assert P.text_line_at(pdf, 0, 500, 700) is None
    edit = {"kind": "replace", "rotation": 0, **line, "text": "Payable by 2 January 2027", "changed": True}
    out, _ = P.build_pdf(pdf, edits={0: [edit]})
    with fitz.open(stream=out) as doc:
        text = doc[0].get_text()
        assert "2 January 2027" in text and "14 December" not in text
        # the lines above and below overlap this one's box and must survive whole, as must the frame
        assert "Invoice number 2291" in text and "Total due: Rs 48,500" in text
        assert doc[0].get_drawings()
    untouched, _ = P.build_pdf(pdf, edits={0: [{**edit, "changed": False}]})
    with fitz.open(stream=untouched) as doc:
        assert "14 December" in doc[0].get_text()


@needs_ocr
def test_clicking_a_scanned_line_gives_its_wording_and_look(results):
    target = next(ln for ln in results[0]["lines"] if "SHS/2026/114" in ln["text"])
    x, y = (target["x0"] + target["x1"]) / 2, (target["y0"] + target["y1"]) / 2
    line = P.text_line_at(SAMPLE, 0, x, y, 0, results[0])
    assert "SHS/2026/114" in line["text"] and line["erase"] == "pixels"
    assert 9 <= line["size"] <= 15  # typeset at 13pt; not inflated by the gap before "Date"
    assert P.text_line_at(SAMPLE, 0, x, y, 0, None) is None  # an unread scan has no text to pick


def test_shapes_lines_stamps_and_markup_are_drawn_where_placed():
    blank = fitz.open()
    blank.new_page()
    pdf = blank.tobytes()
    edits = [
        {"kind": "rect", "rect": [100, 100, 200, 160], "rotation": 0, "color": "#000000", "width": 3, "fill": ""},
        {"kind": "ellipse", "rect": [300, 100, 400, 160], "rotation": 0, "color": "#000000", "width": 0,
         "fill": "#000000", "opacity": 100},
        {"kind": "arrow", "rect": [100, 300, 300, 300], "line": [100, 300, 300, 300], "rotation": 0,
         "color": "#000000", "width": 3},
        {"kind": "line", "rect": [100, 400, 100, 500], "line": [100, 400, 100, 500], "rotation": 0,
         "color": "#000000", "width": 4},
        {"kind": "stamp", "rect": [300, 400, 500, 460], "rotation": 0, "text": "PAID", "color": "#000000"},
        {"kind": "underline", "rect": [100, 600, 300, 620], "rotation": 0, "color": "#000000"},
        {"kind": "strike", "rect": [100, 650, 300, 670], "rotation": 0, "color": "#000000"},
    ]
    out, _ = P.build_pdf(pdf, edits={0: edits})
    assert _ink(out, (96, 96, 204, 164)) > 300          # rectangle outline
    assert _ink(out, (120, 115, 180, 145)) == 0         # ...and nothing inside it
    assert _ink(out, (330, 115, 370, 145)) > 1000       # filled ellipse
    assert _ink(out, (285, 290, 302, 310)) > 40         # arrow head at the tip
    assert _ink(out, (96, 400, 104, 500)) > 250         # vertical line, although its box has no width
    assert _ink(out, (100, 615, 300, 622)) > 100 and _ink(out, (100, 600, 300, 612)) == 0  # underline at the foot
    assert _ink(out, (100, 656, 300, 664)) > 100        # strike through the middle
    with fitz.open(stream=out) as doc:
        assert "PAID" in doc[0].get_text()
    # an arrow drawn on a turned page stays where it was drawn
    turned, _ = P.build_pdf(pdf, rotations={0: 90}, edits={0: [dict(edits[2], rotation=90)]})
    assert _ink(turned, (285, 290, 302, 310)) > 40


def test_comment_becomes_a_sticky_note():
    pdf = _typed_pdf()
    note = {"kind": "comment", "rect": [420, 80, 438, 98], "rotation": 0, "text": "Check this date"}
    out, _ = P.build_pdf(pdf, edits={0: [note, {**note, "text": "  "}]})
    with fitz.open(stream=out) as doc:
        page = doc[0]  # annotations are only valid while their page object is alive
        notes = list(page.annots())
        assert [n.info["content"] for n in notes] == ["Check this date"]
        assert abs(notes[0].rect.x0 - 420) < 3 and abs(notes[0].rect.y0 - 80) < 3


def test_objects_can_be_picked_moved_and_resized():
    box = {"kind": "rect", "rect": [100, 100, 300, 300], "rotation": 0, "fill": ""}
    filled = {"kind": "cover", "rect": [100, 100, 300, 300], "rotation": 0}
    arrow = {"kind": "arrow", "rect": [0, 0, 200, 200], "line": [0, 0, 200, 200], "rotation": 0}
    assert P.hit_test([box], 101, 200) == 0          # on the outline
    assert P.hit_test([box], 200, 200) is None       # inside an unfilled outline: reach what it surrounds
    assert P.hit_test([box, filled], 200, 200) == 1  # topmost first
    assert P.hit_test([arrow], 100, 102) == 0 and P.hit_test([arrow], 180, 20) is None
    shifted = P.moved(arrow, 10, -5)
    assert shifted["rect"] == [10, -5, 210, 195] and shifted["line"] == [10, -5, 210, 195]
    assert arrow["line"] == [0, 0, 200, 200]         # the original is left alone, for undo
    assert P.resized(arrow, [0, 0, 100, 400])["line"] == [0, 0, 100, 400]
    # an object drawn before the page was turned can be found at its new place
    pdf = _typed_pdf()
    there = P.transformed({"kind": "cover", "rect": [60, 66, 260, 84]}, P.rotation_map(pdf, 0, 0, 90))
    x0, y0, x1, y1 = there["rect"]
    assert _ink(pdf, (x0, y0, x1, y1), rotation=90) > 200  # the first line of text really is there
    back = P.carry(((x0 + x1) / 2, (y0 + y1) / 2), P.rotation_map(pdf, 0, 90, 0))
    assert abs(back[0] - 160) < 0.01 and abs(back[1] - 75) < 0.01


def test_form_fields_are_listed_and_filled():
    doc = fitz.open()
    page = doc.new_page()
    for kind, name, rect in ((fitz.PDF_WIDGET_TYPE_TEXT, "name", (60, 100, 300, 122)),
                             (fitz.PDF_WIDGET_TYPE_CHECKBOX, "agree", (60, 140, 76, 156)),
                             (fitz.PDF_WIDGET_TYPE_COMBOBOX, "city", (60, 170, 200, 190))):
        widget = fitz.Widget()
        widget.field_type, widget.field_name, widget.rect = kind, name, fitz.Rect(rect)
        if name == "city":
            widget.choice_values = ["Pune", "Mumbai"]
        page.add_widget(widget)
    pdf = doc.tobytes()
    fields = {f["name"]: f for f in P.form_fields(pdf)}
    assert [fields[n]["kind"] for n in ("name", "agree", "city")] == ["text", "checkbox", "choice"]
    assert fields["city"]["options"] == ["Pune", "Mumbai"] and fields["agree"]["value"] is False
    assert P.form_fields(_typed_pdf()) == []
    out, _ = P.build_pdf(pdf, fields={"name": "Asha Rao", "agree": True, "city": "Mumbai"})
    filled = {f["name"]: f["value"] for f in P.form_fields(out)}
    assert filled == {"name": "Asha Rao", "agree": True, "city": "Mumbai"}
    assert _ink(out, (60, 100, 300, 122)) > 50  # the typed name shows on the page


def test_blank_and_duplicate_pages_and_signature_cleanup():
    pdf = _typed_pdf()
    longer = P.add_blank_page(P.duplicate_page(pdf, 0))
    with fitz.open(stream=longer) as doc:
        assert len(doc) == 3 and doc[2].rect == doc[0].rect
        assert "Invoice number 2291" in doc[1].get_text() and not doc[2].get_text().strip()
    full = fitz.open()
    for _ in range(P.MAX_PAGES):
        full.new_page()
    with pytest.raises(P.PdfError):
        P.add_blank_page(full.tobytes())
    import io
    from PIL import Image, ImageDraw
    paper = Image.new("RGB", (120, 60), "white")
    ImageDraw.Draw(paper).line([10, 30, 110, 30], fill=(20, 20, 60), width=6)
    raw = io.BytesIO()
    paper.save(raw, "PNG")
    clean = Image.open(io.BytesIO(P.transparent_background(raw.getvalue())))
    assert clean.mode == "RGBA" and clean.getpixel((5, 5))[3] == 0 and clean.getpixel((60, 30))[3] == 255
    assert P.image_size(raw.getvalue()) == (120, 60)
    with pytest.raises(P.PdfError):
        P.image_size(b"not a picture")
