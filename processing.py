"""Document processing for FolioDesk: validation, rendering, OCR and PDF export.

Nothing in this module imports Streamlit, so it can be tested and reused on its own.

Coordinate convention: every box stored in a page result is in PDF points in
*display space*, i.e. the page as it looks after its rotation is applied, with
the origin at the top-left. Export converts back to the PDF's unrotated space.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Any, Callable, Iterable

import fitz
import pytesseract
from PIL import Image, ImageDraw, ImageOps
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
            raise PdfError("This PDF is password-protected. Remove the password and upload it again.")
        count = len(doc)
    if count == 0:
        raise PdfError("This PDF has no pages.")
    if count > MAX_PAGES:
        raise PdfError(
            f"This PDF has {count} pages. The limit is {MAX_PAGES}. Split it and upload one part at a time."
        )
    return count


def _open_page(pdf_bytes: bytes, page_no: int, extra_rotation: int) -> tuple[fitz.Document, fitz.Page]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_no]
    if extra_rotation % 360:
        page.set_rotation((page.rotation + extra_rotation) % 360)
    return doc, page


def render_page(pdf_bytes: bytes, page_no: int, dpi: int = 110, extra_rotation: int = 0) -> Image.Image:
    """Render one page as it will look after `extra_rotation` is applied."""
    doc, page = _open_page(pdf_bytes, page_no, extra_rotation)
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
    return fitz.Font("helv")


def _font_for(text: str) -> fitz.Font:
    if any("\u0900" <= ch <= "\u097f" for ch in text):
        return _font("devanagari")
    return _font("latin")


def _write(page: fitz.Page, writer: fitz.TextWriter, **kwargs: Any) -> None:
    """Write display-space text onto the page, whatever its rotation.

    `writer` must have been created with `page.rect`. PyMuPDF places TextWriter
    output in the page's unrotated space, so on a rotated page the text is
    carried there with the page's derotation matrix (expressed in PDF's
    bottom-left coordinates, and undoing the shift write_text adds itself).
    """
    if page.rotation == 0:
        writer.write_text(page, overlay=True, **kwargs)
        return
    unrotated_height = page.cropbox.height
    matrix = (
        writer.ictm
        * page.derotation_matrix
        * fitz.Matrix(1, 0, 0, -1, 0, unrotated_height)
        * fitz.Matrix(1, 0, 0, 1, 0, page.rect.height - unrotated_height)
    )
    writer.write_text(page, matrix=matrix, overlay=True, **kwargs)


def _fit(writer: fitz.TextWriter, text: str, x0: float, x1: float, baseline: float, height: float) -> None:
    """Place `text` so it spans x0..x1, keeping selections aligned with the scan."""
    font = _font_for(text)
    unit = font.text_length(text, fontsize=1)
    if unit <= 0:
        return
    size = max(2.0, min((x1 - x0) / unit, height * 1.6))
    try:
        # The trailing space keeps neighbouring words separate when text is copied or searched.
        writer.append(fitz.Point(x0, baseline), text + " ", font=font, fontsize=size)
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


def build_pdf(pdf_bytes: bytes, rotations: dict[int, int] | None = None,
              removed: Iterable[int] = (), notes: list[dict[str, Any]] | None = None,
              results: dict[int, dict[str, Any]] | None = None,
              edited_text: dict[int, str] | None = None,
              text_layer: bool = False) -> tuple[bytes, dict[str, Any]]:
    """Apply page edits and, optionally, a searchable text layer.

    Returns the PDF and a small report: pages kept and how each text layer was aligned.
    """
    rotations, results, edited_text = rotations or {}, results or {}, edited_text or {}
    removed = set(removed)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    keep = [p for p in range(len(doc)) if p not in removed]
    if not keep:
        raise PdfError("Every page is marked as removed. Restore at least one page to export.")
    layers: dict[int, str] = {}
    for page_no in keep:
        page = doc[page_no]
        extra = rotations.get(page_no, 0) % 360
        if extra:
            page.set_rotation((page.rotation + extra) % 360)
        result = results.get(page_no)
        # Pages that already carry text need no layer; stale results (page rotated
        # since it was recognised) would be misplaced, so they are skipped too.
        if text_layer and result and result["source"] == "ocr" and result["rotation"] == extra:
            text = edited_text.get(page_no)
            if (text if text is not None else result["text"]).strip():
                layers[page_no] = _add_text_layer(page, result, text)
        for note in notes or []:
            if note["page"] == page_no:
                _add_note(page, note)
    doc.select(keep)
    if layers or notes:
        doc.subset_fonts()  # embed only the glyphs used, not whole fonts
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data, {"pages": len(keep), "layers": layers}


def build_text(page_count: int, texts: dict[int, str], removed: Iterable[int] = ()) -> str:
    """Plain-text export with a heading per page."""
    removed = set(removed)
    parts = [
        f"--- Page {p + 1} ---\n{texts[p].strip()}"
        for p in range(page_count)
        if p in texts and p not in removed and texts[p].strip()
    ]
    return "\n\n".join(parts) + ("\n" if parts else "")


def safe_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name.rsplit(".", 1)[0]).strip("-.") or "document"
    return stem[:70]
