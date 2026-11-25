"""Document processing for FolioDesk: validation, rendering, OCR and PDF export.

Nothing in this module imports Streamlit, so it can be tested and reused on its own.

Coordinate convention: every box stored in a page result is in PDF points in
*display space*, i.e. the page as it looks after its rotation is applied, with
the origin at the top-left. Export converts back to the PDF's unrotated space.
"""
from __future__ import annotations

import io
import math
import os
import re
import shutil
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Any, Callable, Iterable

import fitz
import pytesseract
from PIL import Image, ImageChops, ImageDraw, ImageOps
from pytesseract import Output

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_OCR_PIXELS = 12_000_000  # keeps memory bounded on small instances
LOW_CONFIDENCE = 60.0
MIN_EMBEDDED_CHARS = 25

LANGUAGES = {
    # English first: with Hindi first, Tesseract drops the digit 1 from Latin text.
    "Hindi + English": "eng+hin",
    "English": "eng",
    "Hindi": "hin",
    "French": "fra",
    "German": "deu",
    "Spanish": "spa",
}

QUALITY_DPI = {"Fast": 200, "Standard": 300, "Fine print": 400}

NOTE_POSITIONS = ("Top left", "Top right", "Bottom left", "Bottom right")

_FONT_CANDIDATES = {
    "devanagari": (
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.otf",
        "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
        "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
        "C:/Windows/Fonts/Nirmala.ttf",
    ),
    "devanagari-bold": (
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.otf",
    ),
    "latin-bold": (
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ),
    "latin": (
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ),
}


class PdfError(ValueError):
    """A problem with the uploaded file that the user can act on."""


class PdfLocked(PdfError):
    """The PDF needs a password before it can be opened."""


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------

def tesseract_status() -> dict[str, Any]:
    """Report whether Tesseract runs and which language packs it has."""
    path = shutil.which("tesseract")
    if not path:
        return {"ok": False, "detail": "Tesseract is not installed on this machine.", "langs": []}
    try:
        version = subprocess.check_output(
            [path, "--version"], stderr=subprocess.STDOUT, text=True, timeout=5
        ).splitlines()[0]
        listed = subprocess.check_output(
            [path, "--list-langs"], stderr=subprocess.STDOUT, text=True, timeout=5
        ).splitlines()[1:]
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        return {"ok": False, "detail": f"Tesseract could not start: {exc}", "langs": []}
    return {"ok": True, "detail": version, "langs": sorted(x.strip() for x in listed if x.strip())}


def available_languages(installed: Iterable[str]) -> list[str]:
    """Language choices whose Tesseract data is actually installed."""
    have = set(installed)
    return [label for label, code in LANGUAGES.items() if set(code.split("+")) <= have]


def default_workers() -> int:
    """Parallel OCR processes. Override with OCR_WORKERS on small instances."""
    try:
        return max(1, int(os.environ["OCR_WORKERS"]))
    except (KeyError, ValueError):
        return max(1, min(2, os.cpu_count() or 1))


# --------------------------------------------------------------------------
# Opening and rendering
# --------------------------------------------------------------------------

def inspect_pdf(pdf_bytes: bytes) -> int:
    """Validate an upload and return its page count, or raise PdfError."""
    if len(pdf_bytes) > MAX_BYTES:
        raise PdfError(
            f"This file is {len(pdf_bytes) / 1_048_576:.1f} MB. The limit is "
            f"{MAX_BYTES // 1_048_576} MB. Compress it or split it and try again."
        )
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfError("This file could not be opened as a PDF. Check that it is not damaged.") from exc
    with doc:
        if doc.needs_pass:
            raise PdfLocked("This PDF is password-protected. Enter its password to open it.")
        count = len(doc)
    if count == 0:
        raise PdfError("This PDF has no pages.")
    if count > MAX_PAGES:
        raise PdfError(
            f"This PDF has {count} pages. The limit is {MAX_PAGES}. Split it and upload one part at a time."
        )
    return count


def unlock_pdf(pdf_bytes: bytes, password: str) -> bytes:
    """Return an unprotected copy of a password-protected PDF."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfError("This file could not be opened as a PDF. Check that it is not damaged.") from exc
    with doc:
        if doc.needs_pass and not doc.authenticate(password):
            raise PdfLocked("That password did not open the file. Check it and try again.")
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE, garbage=3, deflate=True)


def merge_pdfs(base: bytes, extra: bytes) -> tuple[bytes, int]:
    """Append the pages of `extra` to `base`. Returns the new PDF and how many pages were added."""
    added = inspect_pdf(extra)
    doc = fitz.open(stream=base, filetype="pdf")
    with doc, fitz.open(stream=extra, filetype="pdf") as other:
        if len(doc) + added > MAX_PAGES:
            raise PdfError(
                f"Adding {added} pages would make {len(doc) + added}. The limit is {MAX_PAGES} pages."
            )
        doc.insert_pdf(other)
        return doc.tobytes(garbage=3, deflate=True), added


def _open_page(pdf_bytes: bytes, page_no: int, extra_rotation: int) -> tuple[fitz.Document, fitz.Page]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_no]
    if extra_rotation % 360:
        page.set_rotation((page.rotation + extra_rotation) % 360)
    return doc, page


def render_page(pdf_bytes: bytes, page_no: int, dpi: int = 110, extra_rotation: int = 0,
                edits: list[dict[str, Any]] | None = None,
                fields: dict[str, Any] | None = None) -> Image.Image:
    """Render one page as it will look after `extra_rotation`, form `fields` and `edits` are applied."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_no]
    base = page.rotation
    if fields:
        _fill_fields(page, fields)
    if edits:
        apply_edits(page, edits, base)
    page.set_rotation((base + extra_rotation) % 360)
    with doc:
        scale = dpi / 72
        area = page.rect.width * page.rect.height * scale * scale
        if area > MAX_OCR_PIXELS:
            scale *= (MAX_OCR_PIXELS / area) ** 0.5
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def highlight_words(image: Image.Image, page_size: tuple[float, float], words: list[dict[str, Any]],
                    threshold: float = LOW_CONFIDENCE) -> Image.Image:
    """Mark words Tesseract was unsure about, like a proofreader's highlighter."""
    doubtful = [w for w in words if 0 <= w["conf"] < threshold]
    if not doubtful:
        return image
    sx = image.width / page_size[0]
    sy = image.height / page_size[1]
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    pad = 1.5
    for w in doubtful:
        box = (w["x0"] * sx - pad, w["y0"] * sy - pad, w["x1"] * sx + pad, w["y1"] * sy + pad)
        draw.rectangle(box, fill=(255, 214, 10, 105))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def dim_image(image: Image.Image, amount: float = 0.72) -> Image.Image:
    """Fade a thumbnail towards white, used for pages marked as removed."""
    return Image.blend(image, Image.new("RGB", image.size, "white"), amount)


# --------------------------------------------------------------------------
# Recognition
# --------------------------------------------------------------------------

def _prepare_for_ocr(image: Image.Image) -> Image.Image:
    """Greyscale and stretch contrast: helps faint or yellowed scans."""
    return ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)


def detect_rotation(pdf_bytes: bytes, page_no: int, extra_rotation: int = 0) -> int:
    """Degrees (0/90/180/270) to rotate the page clockwise so its text is upright.

    Uses Tesseract's orientation detection. Returns 0 when unsure.
    """
    image = _prepare_for_ocr(render_page(pdf_bytes, page_no, 150, extra_rotation))
    try:
        osd = pytesseract.image_to_osd(image, output_type=Output.DICT)
    except Exception:  # noqa: BLE001 - too little text, or osd data missing
        return 0
    if float(osd.get("orientation_conf", 0)) < 2.0:
        return 0
    return int(osd.get("rotate", 0)) % 360


def _new_line(x0: float, y0: float, x1: float, y1: float, gap: bool) -> dict[str, Any]:
    return {"words": [], "x0": x0, "y0": y0, "x1": x1, "y1": y1, "gap": gap}


def _extend_line(line: dict[str, Any], text: str, x0: float, y0: float, x1: float, y1: float) -> None:
    line["words"].append(text)
    line["x0"], line["y0"] = min(line["x0"], x0), min(line["y0"], y0)
    line["x1"], line["y1"] = max(line["x1"], x1), max(line["y1"], y1)


def _finish_result(words: list[dict[str, Any]], lines: list[dict[str, Any]], source: str) -> dict[str, Any]:
    out_lines, parts = [], []
    for line in lines:
        text = " ".join(line.pop("words"))
        if line.pop("gap") and parts:
            parts.append("")  # blank line between paragraphs
        parts.append(text)
        out_lines.append({**line, "text": text})
    scored = [w["conf"] for w in words if w["conf"] >= 0]
    return {
        "text": "\n".join(parts),
        "words": words,
        "lines": out_lines,
        "confidence": sum(scored) / len(scored) if scored else 0.0,
        "low_count": sum(1 for c in scored if c < LOW_CONFIDENCE),
        "source": source,
    }


def _embedded_result(page: fitz.Page) -> dict[str, Any] | None:
    """Use the text a PDF already carries instead of re-recognising it."""
    raw = page.get_text("words")
    if sum(len(w[4]) for w in raw) < MIN_EMBEDDED_CHARS:
        return None
    words, lines, last_key, last_block = [], [], None, None
    to_display = page.rotation_matrix  # get_text reports unrotated coordinates
    for *box, text, block, line, _ in raw:
        x0, y0, x1, y1 = fitz.Rect(box) * to_display
        words.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "conf": 100.0})
        if (block, line) != last_key:
            lines.append(_new_line(x0, y0, x1, y1, last_block is not None and block != last_block))
            last_key, last_block = (block, line), block
        _extend_line(lines[-1], text, x0, y0, x1, y1)
    return _finish_result(words, lines, source="embedded")


def recognise_page(pdf_bytes: bytes, page_no: int, lang: str, dpi: int = 300,
                   extra_rotation: int = 0, use_embedded: bool = True) -> dict[str, Any]:
    """Recognise one page. Returns text, word boxes, line boxes and confidence."""
    started = time.perf_counter()
    doc, page = _open_page(pdf_bytes, page_no, extra_rotation)
    with doc:
        size = (page.rect.width, page.rect.height)
        result = _embedded_result(page) if use_embedded else None
    if result is None:
        image = _prepare_for_ocr(render_page(pdf_bytes, page_no, dpi, extra_rotation))
        sx, sy = size[0] / image.width, size[1] / image.height
        data = pytesseract.image_to_data(
            image, lang=lang, output_type=Output.DICT, config=f"--oem 1 --psm 3 --dpi {dpi}"
        )
        words, lines, last_key, last_par = [], [], None, None
        for i, raw in enumerate(data["text"]):
            text = str(raw).strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            x0, y0 = data["left"][i] * sx, data["top"][i] * sy
            x1, y1 = x0 + data["width"][i] * sx, y0 + data["height"][i] * sy
            words.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "conf": conf})
            par = (data["block_num"][i], data["par_num"][i])
            key = (*par, data["line_num"][i])
            if key != last_key:
                lines.append(_new_line(x0, y0, x1, y1, last_par is not None and par != last_par))
                last_key, last_par = key, par
            _extend_line(lines[-1], text, x0, y0, x1, y1)
        result = _finish_result(words, lines, source="ocr")
    result.update(page=page_no, size=size, rotation=extra_rotation % 360, lang=lang, dpi=dpi,
                  seconds=time.perf_counter() - started)
    return result


def recognise_pages(pdf_bytes: bytes, pages: list[int], lang: str, dpi: int = 300,
                    rotations: dict[int, int] | None = None, use_embedded: bool = True,
                    auto_rotate: bool = False, workers: int | None = None,
                    on_done: Callable[[int, dict[str, Any] | None, Exception | None], None] | None = None,
                    ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    """Recognise several pages in parallel.

    Returns (results by page, error message by page). `on_done` is called in the
    calling thread as each page finishes, so it is safe to update a UI from it.
    With `auto_rotate`, a sideways page is turned upright first; the rotation
    used is recorded in the page's result under "rotation".
    """
    rotations = rotations or {}
    workers = min(workers or default_workers(), max(1, len(pages)))

    def work(page_no: int) -> dict[str, Any]:
        rotation = rotations.get(page_no, 0)
        if auto_rotate:
            rotation = (rotation + detect_rotation(pdf_bytes, page_no, rotation)) % 360
        return recognise_page(pdf_bytes, page_no, lang, dpi, rotation, use_embedded)

    results: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}
    # Each Tesseract process is multi-threaded; cap it so parallel pages don't fight.
    previous = os.environ.get("OMP_THREAD_LIMIT")
    if workers > 1:
        os.environ["OMP_THREAD_LIMIT"] = "2"
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, p): p for p in pages}
            for future in as_completed(futures):
                page_no = futures[future]
                try:
                    results[page_no] = future.result()
                    if on_done:
                        on_done(page_no, results[page_no], None)
                except Exception as exc:  # noqa: BLE001 - reported per page
                    errors[page_no] = _friendly_ocr_error(exc)
                    if on_done:
                        on_done(page_no, None, exc)
    finally:
        if workers > 1:
            if previous is None:
                os.environ.pop("OMP_THREAD_LIMIT", None)
            else:
                os.environ["OMP_THREAD_LIMIT"] = previous
    return results, errors


def _friendly_ocr_error(exc: Exception) -> str:
    message = str(exc)
    if "Failed loading language" in message or "Error opening data file" in message:
        return "The language data for this choice is not installed."
    return message.strip().splitlines()[-1][:200] if message.strip() else type(exc).__name__


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _font(kind: str) -> fitz.Font:
    for path in _FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            try:
                return fitz.Font(fontfile=path)
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
    if kind.endswith("-bold"):
        return _font(kind[:-5])
    return fitz.Font("helv")


def _is_devanagari(text: str) -> bool:
    return any("\u0900" <= ch <= "\u097f" for ch in text)


def _font_for(text: str) -> fitz.Font:
    return _font("devanagari" if _is_devanagari(text) else "latin")


FONT_FAMILIES = ("sans", "serif", "mono")
# PDF's built-in faces, by family: regular, bold, italic, bold italic.
_BASE14 = {
    "sans": ("helv", "hebo", "heit", "hebi"),
    "serif": ("tiro", "tibo", "tiit", "tibi"),
    "mono": ("cour", "cobo", "coit", "cobi"),
}


@lru_cache(maxsize=12)
def _base14(code: str) -> fitz.Font:
    return fitz.Font(code)


def _face(text: str, family: str = "sans", bold: bool = False, italic: bool = False) -> fitz.Font:
    """The font for text the user adds: a built-in face when it has every
    character, otherwise a system font with wider coverage (Hindi, symbols)."""
    if _is_devanagari(text):
        return _font("devanagari-bold" if bold else "devanagari")
    face = _base14(_BASE14.get(family, _BASE14["sans"])[int(bool(bold)) + 2 * int(bool(italic))])
    if all(ch.isspace() or face.has_glyph(ord(ch)) for ch in text):
        return face
    return _font("latin-bold" if bold else "latin")


def _write(page: fitz.Page, writer: fitz.TextWriter, turn: tuple[fitz.Point, float] | None = None,
           **kwargs: Any) -> None:
    """Write display-space text onto the page, whatever its rotation.

    `writer` must have been created with `page.rect`. PyMuPDF places TextWriter
    output in the page's unrotated space, so on a rotated page the text is
    carried there with the page's derotation matrix (expressed in PDF's
    bottom-left coordinates, and undoing the shift write_text adds itself).
    `turn` optionally rotates the text by an angle about a display-space point.
    """
    matrix = fitz.Matrix(1, 1)
    if turn:
        pivot = turn[0] * writer.ictm
        shift = fitz.Matrix(1, 1).pretranslate(pivot.x, pivot.y)
        matrix = ~shift * fitz.Matrix(turn[1]) * shift
    if page.rotation:
        unrotated_height = page.cropbox.height
        matrix = (
            matrix
            * writer.ictm
            * page.derotation_matrix
            * fitz.Matrix(1, 0, 0, -1, 0, unrotated_height)
            * fitz.Matrix(1, 0, 0, 1, 0, page.rect.height - unrotated_height)
        )
    if turn or page.rotation:
        writer.write_text(page, matrix=matrix, overlay=True, **kwargs)
    else:
        writer.write_text(page, overlay=True, **kwargs)


def _fit(writer: fitz.TextWriter, text: str, x0: float, x1: float, baseline: float, height: float,
         pad: str = " ") -> None:
    """Place `text` so it spans x0..x1, keeping selections aligned with the scan."""
    font = _font_for(text)
    unit = font.text_length(text, fontsize=1)
    if unit <= 0:
        return
    size = max(2.0, min((x1 - x0) / unit, height * 1.6))
    try:
        # The trailing space keeps neighbouring words separate when text is copied or searched.
        writer.append(fitz.Point(x0, baseline), text + pad, font=font, fontsize=size)
    except Exception:  # noqa: BLE001 - a glyph the font lacks should not sink the export
        pass


def _add_text_layer(page: fitz.Page, result: dict[str, Any], edited: str | None) -> str:
    """Add invisible text. Returns how precisely it could be aligned: word, line or page."""
    writer = fitz.TextWriter(page.rect)
    lines = result["lines"]
    edited_lines = [ln for ln in (edited or "").splitlines() if ln.strip()]
    if edited is None or edited == result["text"]:
        mode = "word"
        for w in result["words"]:
            height = w["y1"] - w["y0"]
            _fit(writer, w["text"], w["x0"], w["x1"], w["y1"] - 0.18 * height, height)
    elif len(edited_lines) == len(lines):
        # Corrections that keep the line structure stay aligned line by line.
        mode = "line"
        for ln, text in zip(lines, edited_lines):
            height = ln["y1"] - ln["y0"]
            _fit(writer, text.strip(), ln["x0"], ln["x1"], ln["y1"] - 0.22 * height, height)
    else:
        mode = "page"
        width, height = result["size"]
        box = fitz.Rect(18, 18, width - 18, height - 18)
        for size in (9, 7, 5, 3.5, 2):
            trial = fitz.TextWriter(page.rect)
            try:
                leftover = trial.fill_textbox(box, edited, font=_font_for(edited), fontsize=size)
            except Exception:  # noqa: BLE001
                leftover = [1]
            if not leftover:
                writer = trial
                break
    _write(page, writer, render_mode=3)
    return mode


def _add_note(page: fitz.Page, note: dict[str, Any]) -> None:
    text = note["text"].strip()
    if not text:
        return
    size = float(note.get("size", 12))
    font = _font_for(text)
    margin = 36
    width = font.text_length(text, fontsize=size)
    position = note.get("position", NOTE_POSITIONS[0])
    x = max(margin / 2, page.rect.width - margin - width) if "right" in position else margin
    y = page.rect.height - margin if "Bottom" in position else margin + size
    writer = fitz.TextWriter(page.rect)
    writer.append(fitz.Point(x, y), text, font=font, fontsize=size)
    _write(page, writer, color=(0.09, 0.18, 0.52))


# -- Page edits ------------------------------------------------------------
#
# An edit is a dict in display space, valid for the rotation it was drawn at.
# Every edit has "kind", "rect" [x0, y0, x1, y1] and "rotation". By kind:
#   text                text, size, color, font, bold, italic, align
#   replace             text, color, fill, size, origin [x, baseline], font, bold, italic,
#                       erase ("text" removes real PDF text, "pixels" paints over a scan),
#                       changed (False while the wording is still the original)
#   highlight, underline, strike, cover     color
#   redact
#   rect, ellipse       color, width, fill ("" for none), opacity (0-100)
#   line, arrow         line [xa, ya, xb, yb], color, width
#   image               data (bytes)
#   stamp               text, color
#   comment             text (a sticky note; rect marks where its icon sits)

EDIT_KINDS = ("text", "replace", "highlight", "underline", "strike", "cover", "redact",
              "rect", "ellipse", "line", "arrow", "image", "stamp", "comment")
_ALIGN = {"left": 0, "center": 1, "right": 2}


def _rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: Iterable[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in rgb)


def apply_edits(page: fitz.Page, edits: list[dict[str, Any]], base_rotation: int) -> None:
    """Draw edits onto a page. Each is applied at the rotation it was drawn at,
    so it stays glued to the content if the page is turned afterwards.
    Leaves the page's rotation changed; the caller sets the final one."""
    for edit in edits:
        kind = edit["kind"]
        if kind == "replace" and edit.get("changed") is False:
            continue  # a line picked for editing whose wording was left alone
        page.set_rotation((base_rotation + edit.get("rotation", 0)) % 360)
        if kind in ("line", "arrow"):
            _draw_line(page, edit)
            continue
        display = fitz.Rect(edit["rect"]).normalize() & page.rect
        if display.is_empty or display.width < 1 or display.height < 1:
            continue
        own = display * page.derotation_matrix  # the page's unrotated space
        own.normalize()
        color = edit.get("color") or "#16202c"
        if kind == "highlight":
            page.draw_rect(own, color=None, fill=_rgb(edit.get("color", "#ffd60a")), fill_opacity=0.4, overlay=True)
        elif kind in ("underline", "strike"):
            y = display.y1 - 0.06 * display.height if kind == "underline" else display.y0 + 0.52 * display.height
            a, b = (fitz.Point(x, y) * page.derotation_matrix for x in (display.x0, display.x1))
            page.draw_line(a, b, color=_rgb(color), width=max(0.7, min(2.5, display.height * 0.07)), overlay=True)
        elif kind == "cover":
            page.draw_rect(own, color=None, fill=_rgb(edit.get("color", "#ffffff")), overlay=True)
        elif kind == "redact":
            page.add_redact_annot(own, fill=(0, 0, 0))
            page.apply_redactions()  # removes the text and image pixels underneath, not just hides them
        elif kind in ("rect", "ellipse"):
            width = float(edit.get("width", 2))
            fill = edit.get("fill") or None
            if width <= 0 and not fill:
                continue
            opacity = max(0.05, min(1.0, float(edit.get("opacity", 100)) / 100))
            draw = page.draw_rect if kind == "rect" else page.draw_oval
            draw(own, color=_rgb(color) if width > 0 else None, fill=_rgb(fill) if fill else None,
                 width=width or 1, stroke_opacity=opacity, fill_opacity=opacity, overlay=True)
        elif kind == "image":
            page.insert_image(own, stream=edit["data"], rotate=page.rotation, keep_proportion=True, overlay=True)
        elif kind == "text":
            _text_in_box(page, display, edit)
        elif kind == "stamp":
            _stamp(page, display, own, edit.get("text", "").strip(), _rgb(edit.get("color", "#1f3fbf")))
        elif kind == "comment":
            if edit.get("text", "").strip():
                _comment(page, display, edit["text"].strip())
        elif kind == "replace":
            _replace(page, display, own, edit)


def _draw_line(page: fitz.Page, edit: dict[str, Any]) -> None:
    xa, ya, xb, yb = edit.get("line") or edit["rect"]
    length = math.hypot(xb - xa, yb - ya)
    if length < 1:
        return
    width = max(0.3, float(edit.get("width", 2)))
    ink = _rgb(edit.get("color") or "#b93520")
    own = page.derotation_matrix
    start, tip = fitz.Point(xa, ya), fitz.Point(xb, yb)
    shape = page.new_shape()
    if edit["kind"] == "arrow":
        head = min(length * 0.6, max(9.0, width * 4.5))
        ux, uy = (xb - xa) / length, (yb - ya) / length
        neck = fitz.Point(xb - ux * head, yb - uy * head)
        wing = head * 0.42
        corners = [fitz.Point(neck.x - uy * wing, neck.y + ux * wing), tip,
                   fitz.Point(neck.x + uy * wing, neck.y - ux * wing)]
        shape.draw_line(start * own, fitz.Point(xb - ux * head * 0.8, yb - uy * head * 0.8) * own)
        shape.finish(color=ink, width=width, closePath=False)
        shape.draw_polyline([p * own for p in corners])
        shape.finish(color=ink, fill=ink, width=0.3, closePath=True)
    else:
        shape.draw_line(start * own, tip * own)
        shape.finish(color=ink, width=width, closePath=False, lineCap=1)
    shape.commit(overlay=True)


def _text_in_box(page: fitz.Page, box: fitz.Rect, edit: dict[str, Any]) -> None:
    """Wrap text inside a box, shrinking it if needed so that all of it fits."""
    text = edit.get("text", "")
    if not text.strip():
        return
    size = float(edit.get("size", 12))
    font = _face(text, edit.get("font", "sans"), edit.get("bold", False), edit.get("italic", False))
    ink = _rgb(edit.get("color") or "#16202c")
    align = _ALIGN.get(edit.get("align", "left"), 0)
    for attempt in (size, size * 0.85, size * 0.7, size * 0.55, size * 0.4, 4):
        writer = fitz.TextWriter(page.rect)
        try:
            leftover = writer.fill_textbox(box, text, font=font, fontsize=attempt, align=align)
        except Exception:  # noqa: BLE001 - box too small for one line at this size
            leftover = [1]
        if not leftover:
            _write(page, writer, color=ink)
            return
    # Too small to wrap at any size: write one line from the box's top-left.
    writer = fitz.TextWriter(page.rect)
    writer.append(fitz.Point(box.x0, box.y0 + size), text.splitlines()[0], font=font, fontsize=size)
    _write(page, writer, color=ink)


def _stamp(page: fitz.Page, display: fitz.Rect, own: fitz.Rect, text: str,
           ink: tuple[float, float, float]) -> None:
    """A rubber stamp: bold wording inside a rounded frame, sized to its box."""
    if not text:
        return
    font = _face(text, "sans", bold=True)
    unit = font.text_length(text, fontsize=1)
    if unit <= 0:
        return
    size = max(4.0, min(display.height * 0.5, display.width * 0.82 / unit))
    page.draw_rect(own, color=ink, width=max(1.0, min(3.0, display.height * 0.06)), radius=0.2, overlay=True)
    writer = fitz.TextWriter(page.rect)
    centre = (display.tl + display.br) / 2
    writer.append(fitz.Point(centre.x - unit * size / 2, centre.y + size * 0.36), text, font=font, fontsize=size)
    _write(page, writer, color=ink)


def _comment(page: fitz.Page, display: fitz.Rect, text: str) -> None:
    """A sticky note that PDF readers show as an icon with pop-up text."""
    annot = page.add_text_annot(display.tl * page.derotation_matrix, text, icon="Comment")
    annot.set_info(title="FolioDesk")
    annot.update()


def _replace(page: fitz.Page, display: fitz.Rect, own: fitz.Rect, edit: dict[str, Any]) -> None:
    """Take existing wording off the page and write new wording in its place."""
    # Real text is removed through a thin band along the middle of the line, so
    # neighbouring lines whose boxes overlap this one keep all their letters.
    band = fitz.Rect(display.x0, display.y0 + 0.3 * display.height, display.x1, display.y1 - 0.3 * display.height)
    page.add_redact_annot((band * page.derotation_matrix).normalize(), fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)
    if edit.get("erase", "pixels") == "pixels":
        # On a scan the letters are part of the picture: paint the paper colour over them.
        page.add_redact_annot(own, fill=_rgb(edit.get("fill", "#ffffff")))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_NONE)
    text = edit.get("text", "").strip()
    if not text:
        return
    writer = fitz.TextWriter(page.rect)
    size = edit.get("size")
    if size:
        font = _face(text, edit.get("font", "sans"), edit.get("bold", False), edit.get("italic", False))
        x, baseline = edit.get("origin") or (display.x0, display.y1 - 0.2 * display.height)
        try:
            writer.append(fitz.Point(x, baseline), text, font=font, fontsize=float(size))
        except Exception:  # noqa: BLE001 - a glyph the font lacks should not sink the export
            return
    else:
        _fit(writer, text, display.x0, display.x1, display.y1 - 0.2 * display.height,
             display.height / 1.6, pad="")
    _write(page, writer, color=_rgb(edit.get("color", "#16202c")))


# -- Geometry helpers for the editor ---------------------------------------

def rotation_map(pdf_bytes: bytes, page_no: int, from_rotation: int, to_rotation: int) -> tuple[float, ...]:
    """A matrix (as six numbers) carrying display-space points of the page at
    one rotation to where they sit at another."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    with doc:
        page = doc[page_no]
        base = page.rotation
        page.set_rotation((base + from_rotation) % 360)
        back = page.derotation_matrix
        page.set_rotation((base + to_rotation) % 360)
        return tuple(back * page.rotation_matrix)


def carry(point: Iterable[float], matrix: Iterable[float]) -> tuple[float, float]:
    """A point carried through a matrix from `rotation_map`."""
    moved_point = fitz.Point(*point) * fitz.Matrix(*matrix)
    return moved_point.x, moved_point.y


def transformed(edit: dict[str, Any], matrix: Iterable[float]) -> dict[str, Any]:
    """A copy of an edit with its geometry carried through `matrix`."""
    m = fitz.Matrix(*matrix)
    out = dict(edit)
    out["rect"] = list((fitz.Rect(edit["rect"]) * m).normalize())
    if edit.get("line"):
        a, b = fitz.Point(edit["line"][:2]) * m, fitz.Point(edit["line"][2:]) * m
        out["line"] = [a.x, a.y, b.x, b.y]
    return out


def moved(edit: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    """A copy of an edit shifted by (dx, dy)."""
    out = dict(edit)
    x0, y0, x1, y1 = edit["rect"]
    out["rect"] = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
    if edit.get("line"):
        xa, ya, xb, yb = edit["line"]
        out["line"] = [xa + dx, ya + dy, xb + dx, yb + dy]
    if edit.get("origin"):
        out["origin"] = [edit["origin"][0] + dx, edit["origin"][1] + dy]
    return out


def resized(edit: dict[str, Any], rect: Iterable[float]) -> dict[str, Any]:
    """A copy of an edit stretched to fill `rect`."""
    old = fitz.Rect(edit["rect"]).normalize()
    new = fitz.Rect(*rect).normalize()
    kx = new.width / old.width if old.width else 1.0
    ky = new.height / old.height if old.height else 1.0

    def carry(x: float, y: float) -> tuple[float, float]:
        return new.x0 + (x - old.x0) * kx, new.y0 + (y - old.y0) * ky

    out = dict(edit)
    out["rect"] = list(new)
    if edit.get("line"):
        out["line"] = [*carry(*edit["line"][:2]), *carry(*edit["line"][2:])]
    if edit.get("origin"):
        out["origin"] = list(carry(*edit["origin"]))
    return out


def hit_test(edits: list[dict[str, Any]], x: float, y: float, reach: float = 4.0) -> int | None:
    """Index of the topmost edit under a point, or None. Outlines without a
    fill are only hit near their edge, so what they surround stays reachable."""
    point = fitz.Point(x, y)
    for i in range(len(edits) - 1, -1, -1):
        edit = edits[i]
        box = fitz.Rect(edit["rect"]).normalize()
        if not fitz.Rect(box.x0 - reach, box.y0 - reach, box.x1 + reach, box.y1 + reach).contains(point):
            continue
        kind = edit["kind"]
        if kind in ("line", "arrow") and edit.get("line"):
            xa, ya, xb, yb = edit["line"]
            span = (xb - xa) ** 2 + (yb - ya) ** 2
            t = 0.0 if span == 0 else max(0.0, min(1.0, ((x - xa) * (xb - xa) + (y - ya) * (yb - ya)) / span))
            if math.hypot(x - (xa + t * (xb - xa)), y - (ya + t * (yb - ya))) > reach + 3:
                continue
        elif kind in ("rect", "ellipse") and not edit.get("fill"):
            edge = reach + 3
            inner = fitz.Rect(box.x0 + edge, box.y0 + edge, box.x1 - edge, box.y1 - edge)
            if inner.x0 < inner.x1 and inner.y0 < inner.y1 and inner.contains(point):
                continue
        return i
    return None


# -- Finding existing text --------------------------------------------------

def _over_image(page: fitz.Page, box: fitz.Rect) -> bool:
    """True when a picture lies under the middle of `box` (display space), as on a scan."""
    centre = (box.tl + box.br) / 2
    for info in page.get_image_info():
        if (fitz.Rect(info["bbox"]) * page.rotation_matrix).normalize().contains(centre):
            return True
    return False


def _fit_size(text: str, box: fitz.Rect, font: fitz.Font | None = None) -> float:
    """The font size at which `text` is as wide as `box`."""
    unit = (font or _face(text)).text_length(text, fontsize=1)
    if unit <= 0:
        return max(4.0, box.height * 0.8)
    return max(2.0, min(box.width / unit, box.height * 1.6))


def _sample(image: Image.Image, scale: float, box: fitz.Rect) -> tuple[list[float], str, str]:
    """A box grown slightly to cover stray pixels, with the paper and ink colours inside it."""
    grown = fitz.Rect(box.x0 - 1, box.y0 - 1.5, box.x1 + 1, box.y1 + 1.5) & fitz.Rect(
        0, 0, image.width / scale, image.height / scale)
    crop = image.crop(tuple(int(v * scale) for v in grown))
    pixels = sorted(crop.getdata(), key=sum) if crop.width and crop.height else [(255, 255, 255)]
    paper = pixels[int(len(pixels) * 0.85)]   # the lighter bulk is the background
    ink = pixels[int(len(pixels) * 0.03)]     # the darkest few percent is the lettering
    return list(grown), _hex(paper), _hex(ink)


def _snapshot(page: fitz.Page, scale: float = 2.0) -> Image.Image:
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def find_text(pdf_bytes: bytes, page_no: int, query: str, extra_rotation: int = 0,
              result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Find `query` on a page. Returns, per match, a display-space box plus what
    is needed to replace it convincingly: how to erase it, the paper and ink
    colours, and a font size at which the same words fill the same width.

    Looks in the PDF's own text first, then in the recognised words of a scan.
    """
    query = query.strip()
    if not query:
        return []
    doc, page = _open_page(pdf_bytes, page_no, extra_rotation)
    with doc:
        boxes = [(fitz.Rect(r) * page.rotation_matrix).normalize() for r in page.search_for(query)]
        real = bool(boxes)
        if not boxes and result and result.get("rotation", 0) == extra_rotation % 360:
            boxes = _find_in_words(result["words"], query)
        if not boxes:
            return []
        image = _snapshot(page)
        matches = []
        for box in boxes:
            grown, paper, ink = _sample(image, 2.0, box)
            pixels = not real or _over_image(page, box)
            matches.append({
                "rect": grown if pixels else list(box), "fill": paper, "color": ink,
                "erase": "pixels" if pixels else "text", "original": query,
                "size": round(_fit_size(query, box), 2),
                "origin": [box.x0, box.y1 - (0.2 if pixels else 0.22) * box.height],
            })
    return matches


def text_line_at(pdf_bytes: bytes, page_no: int, x: float, y: float, extra_rotation: int = 0,
                 result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The line of text under a display-space point, ready to become a "replace"
    edit: its wording, box, look (size, colour, font style) and how to erase it.

    Looks at the PDF's own text first, then at the recognised lines of a scan.
    """
    point = fitz.Point(x, y)
    doc, page = _open_page(pdf_bytes, page_no, extra_rotation)
    with doc:
        to_display = page.rotation_matrix
        zero = fitz.Point(0, 0) * to_display
        found = None
        blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
        for line in (ln for block in blocks for ln in block.get("lines", [])):
            box = (fitz.Rect(line["bbox"]) * to_display).normalize()
            if not fitz.Rect(box.x0 - 1, box.y0 - 1, box.x1 + 1, box.y1 + 1).contains(point):
                continue
            spans = [s for s in line["spans"] if s["text"].strip()]
            heading = fitz.Point(line["dir"]) * to_display - zero
            if not spans or abs(heading.x - 1) > 0.02:
                continue  # only text that runs left to right as the page is shown
            first = spans[0]
            start = fitz.Point(first["origin"]) * to_display
            flags = first["flags"]
            found = {
                "rect": list(box), "text": "".join(s["text"] for s in line["spans"]).strip(),
                "erase": "text", "fill": "#ffffff", "color": f"#{first['color']:06x}",
                "size": round(first["size"], 2), "origin": [start.x, start.y],
                "font": "mono" if flags & 8 else "serif" if flags & 4 else "sans",
                "bold": bool(flags & 16), "italic": bool(flags & 2),
            }
            if _over_image(page, box):
                found["erase"] = "pixels"  # e.g. a scan that already carries a hidden text layer
                del found["size"]          # the hidden layer's size says little about the visible letters
            break
        if found is None and result and result.get("rotation", 0) == extra_rotation % 360:
            for ln in result["lines"]:
                if ln["text"].strip() and ln["x0"] - 2 <= x <= ln["x1"] + 2 and ln["y0"] - 2 <= y <= ln["y1"] + 2:
                    found = {"rect": [ln["x0"], ln["y0"], ln["x1"], ln["y1"]], "text": ln["text"].strip(),
                             "erase": "pixels", "font": "sans", "bold": False, "italic": False}
                    # Size from the words themselves, so wide gaps in the line don't inflate it.
                    face = _face(found["text"])
                    sizes = sorted(
                        (w["x1"] - w["x0"]) / face.text_length(w["text"], fontsize=1)
                        for w in result["words"]
                        if len(w["text"]) >= 3 and ln["x0"] - 1 <= (w["x0"] + w["x1"]) / 2 <= ln["x1"] + 1
                        and ln["y0"] - 1 <= (w["y0"] + w["y1"]) / 2 <= ln["y1"] + 1
                        and face.text_length(w["text"], fontsize=1) > 0)
                    if sizes:
                        found["size"] = round(sizes[len(sizes) // 2], 2)
                    break
        if found is None:
            return None
        if found["erase"] == "pixels":
            box = fitz.Rect(found["rect"])
            found["rect"], found["fill"], found["color"] = _sample(_snapshot(page), 2.0, box)
            face = _face(found["text"], found["font"], found["bold"], found["italic"])
            found.setdefault("size", round(_fit_size(found["text"], box, face), 2))
            found["origin"] = [box.x0, box.y1 - 0.2 * box.height]
    found["original"] = found["text"]
    return found


def _find_in_words(words: list[dict[str, Any]], query: str) -> list[fitz.Rect]:
    """Match a phrase against consecutive recognised words, ignoring case."""
    wanted = query.casefold().split()
    found = []
    for i in range(len(words) - len(wanted) + 1):
        run = words[i:i + len(wanted)]
        texts = [w["text"].casefold() for w in run]
        if len(wanted) == 1:
            hit = wanted[0] in texts[0]
        else:
            hit = (texts[0].endswith(wanted[0]) and texts[-1].startswith(wanted[-1])
                   and texts[1:-1] == wanted[1:-1])
        if hit and max(w["y0"] for w in run) < min(w["y1"] for w in run):  # same line
            found.append(fitz.Rect(min(w["x0"] for w in run), min(w["y0"] for w in run),
                                   max(w["x1"] for w in run), max(w["y1"] for w in run)))
    return found


# -- Pictures, forms and extra pages ----------------------------------------

def image_size(data: bytes) -> tuple[int, int]:
    """Width and height of a picture, or raise PdfError if it cannot be read."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception as exc:  # noqa: BLE001
        raise PdfError("That picture could not be opened. Use a PNG or JPEG file.") from exc


def transparent_background(data: bytes, white: int = 225) -> bytes:
    """Make a picture's white paper see-through, so a photographed signature
    sits on the page as ink rather than as a white patch."""
    try:
        image = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise PdfError("That picture could not be opened. Use a PNG or JPEG file.") from exc
    soft = max(1, white - 60)  # fully solid below this, fading to clear at `white`
    alpha = ImageOps.grayscale(image.convert("RGB")).point(
        lambda v: 255 if v <= soft else 0 if v >= white else int(255 * (white - v) / (white - soft)))
    image.putalpha(ImageChops.darker(alpha, image.getchannel("A")))
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


_FIELD_KINDS = {
    fitz.PDF_WIDGET_TYPE_TEXT: "text", fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    fitz.PDF_WIDGET_TYPE_COMBOBOX: "choice", fitz.PDF_WIDGET_TYPE_LISTBOX: "choice",
}


def form_fields(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """The fillable fields of a PDF form: text boxes, tick boxes and drop-downs."""
    fields = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            for widget in page.widgets() or []:
                kind = _FIELD_KINDS.get(widget.field_type)
                if not kind or not widget.field_name or widget.field_flags & 1:  # 1 = read-only
                    continue
                value = widget.field_value
                if kind == "checkbox":
                    value = value not in (None, False, "", "Off")
                options = [o if isinstance(o, str) else str(o[-1]) for o in (widget.choice_values or [])]
                fields.append({
                    "page": page.number, "name": widget.field_name, "kind": kind,
                    "label": widget.field_label or widget.field_name,
                    "value": value if value is not None else "", "options": options,
                    "multiline": bool(widget.field_flags & 4096),
                })
    return fields


def _fill_fields(page: fitz.Page, values: dict[str, Any]) -> None:
    for widget in page.widgets() or []:
        if widget.field_name not in values or widget.field_type not in _FIELD_KINDS:
            continue
        value = values[widget.field_name]
        widget.field_value = bool(value) if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX else str(value)
        widget.update()


def add_blank_page(pdf_bytes: bytes, like_page: int | None = None) -> bytes:
    """Append an empty page, the same size as `like_page` (default: the last page)."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if len(doc) >= MAX_PAGES:
            raise PdfError(f"This document is at the {MAX_PAGES}-page limit.")
        model = doc[like_page if like_page is not None else len(doc) - 1].rect
        doc.new_page(-1, width=model.width, height=model.height)
        return doc.tobytes(garbage=3, deflate=True)


def duplicate_page(pdf_bytes: bytes, page_no: int) -> bytes:
    """Append a copy of one page. The copy's index is the new last page."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if len(doc) >= MAX_PAGES:
            raise PdfError(f"This document is at the {MAX_PAGES}-page limit.")
        doc.fullcopy_page(page_no, -1)
        return doc.tobytes(garbage=3, deflate=True)


def _add_watermark(page: fitz.Page, text: str) -> None:
    font = _font_for(text)
    width, height = page.rect.width, page.rect.height
    diagonal = (width ** 2 + height ** 2) ** 0.5
    size = min(96.0, 0.7 * diagonal / max(font.text_length(text, fontsize=1), 0.01))
    length = font.text_length(text, fontsize=size)
    centre = fitz.Point(width / 2, height / 2)
    writer = fitz.TextWriter(page.rect)
    writer.append(fitz.Point(centre.x - length / 2, centre.y + size * 0.35), text, font=font, fontsize=size)
    angle = fitz.Point(width, height).unit
    _write(page, writer, turn=(centre, math.degrees(math.atan2(angle.y, angle.x))),
           color=(0.35, 0.38, 0.45), opacity=0.18)


def _add_page_number(page: fitz.Page, number: int, total: int) -> None:
    text = f"{number} / {total}"
    font = _font("latin")
    size = 9.0
    writer = fitz.TextWriter(page.rect)
    x = (page.rect.width - font.text_length(text, fontsize=size)) / 2
    writer.append(fitz.Point(x, page.rect.height - 20), text, font=font, fontsize=size)
    _write(page, writer, color=(0.3, 0.33, 0.4))


def _shrink_images(doc: fitz.Document, max_dpi: int = 130, quality: int = 60) -> None:
    """Re-encode large images as JPEG at a lower resolution. Biggest win on scans."""
    seen: set[int] = set()
    for page in doc:
        for info in page.get_images(full=True):
            xref, smask = info[0], info[1]
            if xref in seen or smask:
                continue
            seen.add(xref)
            try:
                rects = page.get_image_rects(xref)
                pix = fitz.Pixmap(doc, xref)
                if pix.colorspace is None or pix.alpha or not rects:
                    continue
                if pix.colorspace.n > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                image = Image.frombytes("L" if pix.n == 1 else "RGB", (pix.width, pix.height), pix.samples)
                shown = max(max(r.width, r.height) for r in rects) / 72  # inches on the page
                limit = int(shown * max_dpi)
                if max(image.size) > limit > 0:
                    image.thumbnail((limit, limit), Image.LANCZOS)
                out = io.BytesIO()
                image.save(out, "JPEG", quality=quality, optimize=True)
                if out.tell() < len(doc.xref_stream_raw(xref)) * 0.9:
                    page.replace_image(xref, stream=out.getvalue())
            except Exception:  # noqa: BLE001 - leave an image we cannot re-encode as it is
                continue


def build_pdf(pdf_bytes: bytes, rotations: dict[int, int] | None = None,
              removed: Iterable[int] = (), notes: list[dict[str, Any]] | None = None,
              results: dict[int, dict[str, Any]] | None = None,
              edited_text: dict[int, str] | None = None,
              text_layer: bool = False, *,
              order: list[int] | None = None,
              edits: dict[int, list[dict[str, Any]]] | None = None,
              fields: dict[str, Any] | None = None,
              watermark: str = "", page_numbers: bool = False,
              compress: bool = False, password: str = "") -> tuple[bytes, dict[str, Any]]:
    """Apply everything the user asked for and return the finished PDF.

    Pages are identified by their index in `pdf_bytes`; `order` lists them in
    output order. Returns the PDF and a small report: pages kept, how each
    text layer was aligned, and pages left unsearchable because of redactions.
    """
    rotations, results, edited_text, edits = rotations or {}, results or {}, edited_text or {}, edits or {}
    removed = set(removed)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    keep = [p for p in (order if order is not None else range(len(doc))) if p not in removed]
    if not keep:
        raise PdfError("Every page is marked as removed. Restore at least one page to export.")
    layers: dict[int, str] = {}
    withheld: list[int] = []
    for position, page_no in enumerate(keep, start=1):
        page = doc[page_no]
        base = page.rotation
        extra = rotations.get(page_no, 0) % 360
        page_edits = edits.get(page_no, [])
        if fields:
            _fill_fields(page, fields)
        redacted = any(e["kind"] == "redact" for e in page_edits)
        result = results.get(page_no)
        # Pages that already carry text need no layer; stale results (page rotated
        # since it was recognised) would be misplaced, so they are skipped too.
        wants_layer = bool(text_layer and result and result["source"] == "ocr" and result["rotation"] == extra)
        if wants_layer and redacted:
            # A hidden text layer could still hold the words under a redaction.
            withheld.append(page_no)
        elif wants_layer:
            page.set_rotation((base + extra) % 360)
            text = edited_text.get(page_no)
            if (text if text is not None else result["text"]).strip():
                layers[page_no] = _add_text_layer(page, result, text)
        if page_edits:
            apply_edits(page, page_edits, base)
        page.set_rotation((base + extra) % 360)
        for note in notes or []:
            if note["page"] == page_no:
                _add_note(page, note)
        if watermark.strip():
            _add_watermark(page, watermark.strip())
        if page_numbers:
            _add_page_number(page, position, len(keep))
    doc.select(keep)
    if compress:
        _shrink_images(doc)
    doc.subset_fonts()  # embed only the glyphs used, not whole fonts
    options: dict[str, Any] = {"garbage": 4 if compress else 3, "deflate": True}
    if password:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    data = doc.tobytes(**options)
    doc.close()
    return data, {"pages": len(keep), "layers": layers, "withheld": withheld}


def pages_as_images(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    """A zip with one PNG per page of a finished PDF."""
    out = io.BytesIO()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc, zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for i, page in enumerate(doc, start=1):
            archive.writestr(f"page-{i:02d}.png", page.get_pixmap(dpi=dpi, alpha=False).tobytes("png"))
    return out.getvalue()


def pages_as_pdfs(pdf_bytes: bytes) -> bytes:
    """A zip with each page of a finished PDF as its own file."""
    out = io.BytesIO()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for i in range(len(doc)):
            single = fitz.open()
            single.insert_pdf(doc, from_page=i, to_page=i)
            archive.writestr(f"page-{i + 1:02d}.pdf", single.tobytes(garbage=3, deflate=True))
            single.close()
    return out.getvalue()


def build_text(pages: int | list[int], texts: dict[int, str], removed: Iterable[int] = ()) -> str:
    """Plain-text export with a heading per page. `pages` is a count or the page order."""
    removed = set(removed)
    kept = [p for p in (range(pages) if isinstance(pages, int) else pages) if p not in removed]
    parts = [
        f"--- Page {number} ---\n{texts[p].strip()}"
        for number, p in enumerate(kept, start=1)
        if p in texts and texts[p].strip()
    ]
    return "\n\n".join(parts) + ("\n" if parts else "")


def safe_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name.rsplit(".", 1)[0]).strip("-.") or "document"
    return stem[:70]
