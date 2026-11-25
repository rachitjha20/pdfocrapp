from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
from typing import Any

import fitz
import streamlit as st
from PIL import Image
import pytesseract
from pytesseract import Output

APP_NAME = "FolioDesk"
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10

st.set_page_config(
    page_title="FolioDesk · Document workshop",
    page_icon="▤",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# A restrained document-workshop aesthetic: warm paper, ink, violet and acid-lime.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
:root { --ink:#191821; --muted:#777482; --paper:#f5f3ee; --panel:#fffefa; --line:#e6e2da; --violet:#8058e8; --lime:#d4f36a; }
html, body, [class*="css"] { font-family:'DM Sans',sans-serif; color:var(--ink); }
.stApp { background:var(--paper); }
.block-container { max-width:1440px; padding-top:1.35rem; padding-bottom:3rem; }
header[data-testid="stHeader"] { background:transparent; }
#MainMenu, footer { visibility:hidden; }
.brandline { display:flex; align-items:center; gap:12px; padding:4px 0 18px; border-bottom:1px solid var(--line); margin-bottom:26px; }
.brandmark { width:38px; height:38px; border-radius:11px; display:grid; place-items:center; background:var(--ink); color:var(--lime); font-size:22px; font-weight:700; }
.brandname { font:800 19px 'Manrope',sans-serif; letter-spacing:-.7px; }
.brandmeta { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:1.6px; margin-left:auto; font-family:'DM Mono',monospace; }
.eyebrow { color:var(--violet); text-transform:uppercase; letter-spacing:2px; font:500 10px 'DM Mono',monospace; margin-bottom:8px; }
.hero-title { font:800 clamp(30px,4vw,48px) 'Manrope',sans-serif; letter-spacing:-2.2px; line-height:1.05; margin:0; color:var(--ink); }
.hero-copy { color:var(--muted); font-size:14px; max-width:620px; line-height:1.7; margin-top:12px; }
.section-kicker { font:500 11px 'DM Mono',monospace; text-transform:uppercase; letter-spacing:1.4px; color:var(--muted); margin:18px 0 10px; }
.paper-card { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:18px 20px; }
.metric { font:800 25px 'Manrope',sans-serif; letter-spacing:-1px; }
.metric-label { color:var(--muted); text-transform:uppercase; font:500 10px 'DM Mono',monospace; letter-spacing:1.1px; margin-top:3px; }
.note { border-left:3px solid var(--lime); background:#fbfbeF; padding:10px 13px; color:#55515e; font-size:12px; line-height:1.55; border-radius:0 8px 8px 0; }
.step-tag { display:inline-block; background:#eeebf8; color:#5f42b5; border-radius:5px; padding:4px 7px; font:500 10px 'DM Mono',monospace; letter-spacing:.5px; }
div[data-testid="stFileUploader"] { background:#fffefa; border:1px dashed #c9c2d7; border-radius:14px; padding:12px; }
div[data-testid="stFileUploader"] section { background:transparent; }
.stButton > button, .stDownloadButton > button { border-radius:8px; border:1px solid var(--ink); background:var(--ink); color:white; font-weight:600; min-height:42px; padding:0 17px; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color:var(--violet); background:var(--violet); color:white; }
button[kind="secondary"] { background:#fffefa; color:var(--ink); border:1px solid var(--line); }
.stTextArea textarea, .stTextInput input, .stSelectbox div[data-baseweb="select"] { background:#fffefa; border-color:var(--line); border-radius:8px; }
.stTabs [data-baseweb="tab-list"] { gap:8px; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { background:transparent; border-radius:8px 8px 0 0; padding:12px 15px; color:var(--muted); font-weight:600; }
.stTabs [aria-selected="true"] { color:var(--violet) !important; border-bottom:2px solid var(--violet); }
div[data-testid="stAlert"] { border-radius:10px; }
div[data-testid="stProgress"] > div > div { background:var(--violet); }
hr { border-color:var(--line); }
.small-mono { color:var(--muted); font:11px 'DM Mono',monospace; }
.preview-frame { background:#e9e6df; border:1px solid var(--line); border-radius:12px; padding:12px; text-align:center; }
.footerline { margin-top:40px; padding-top:14px; border-top:1px solid var(--line); color:#928e99; font:10px 'DM Mono',monospace; letter-spacing:1px; text-transform:uppercase; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="brandline">
  <div class="brandmark">F</div>
  <div><div class="brandname">FolioDesk</div><div class="small-mono">DOCUMENT WORKSHOP / POC 01</div></div>
  <div class="brandmeta">OCR · PAGE TOOLS · EXPORT</div>
</div>
<div class="eyebrow">A practical workspace for scanned documents</div>
<h1 class="hero-title">Make every page<br>work a little harder.</h1>
<p class="hero-copy">Bring in a PDF, recognise its text, correct the result, and export a searchable copy. Or make straightforward page-level changes before you send it on.</p>
""", unsafe_allow_html=True)

def get_tesseract_status() -> tuple[bool, str]:
    path = shutil.which("tesseract")
    if not path:
        return False, "Tesseract is not installed on this server."
    try:
        version = subprocess.check_output([path, "--version"], stderr=subprocess.STDOUT, text=True, timeout=5).splitlines()[0]
        return True, version
    except Exception as exc:
        return False, f"Tesseract could not start: {exc}"

def lang_code(choice: str) -> str:
    return {
        "English": "eng",
        "Hindi": "hin",
        "Hindi + English": "hin+eng",
        "French": "fra",
        "German": "deu",
        "Spanish": "spa",
    }[choice]

def render_page(doc: fitz.Document, page_no: int, scale: float = 1.35) -> Image.Image:
    pix = doc[page_no].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

def ocr_page(image: Image.Image, language: str) -> tuple[str, list[dict[str, Any]]]:
    data = pytesseract.image_to_data(image, lang=language, output_type=Output.DICT, config="--oem 1 --psm 3")
    words = []
    lines = []
    current_line = None
    for i, raw in enumerate(data["text"]):
        word = str(raw).strip()
        if not word:
            continue
        conf = data["conf"][i]
        try:
            confidence = float(conf)
        except (TypeError, ValueError):
            confidence = -1
        item = {
            "text": word,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
            "confidence": confidence,
            "block": int(data["block_num"][i]),
            "par": int(data["par_num"][i]),
            "line": int(data["line_num"][i]),
        }
        words.append(item)
        line_key = (item["block"], item["par"], item["line"])
        if current_line is None or current_line[0] != line_key:
            lines.append([line_key, []])
            current_line = lines[-1]
        current_line[1].append(word)
    text = "\n".join(" ".join(parts) for _, parts in lines)
    return text, words

def make_searchable_pdf(pdf_bytes: bytes, corrected_text: dict[int, str], lang: str) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page_no, text in corrected_text.items():
        if page_no < 0 or page_no >= len(doc) or not text.strip():
            continue
        page = doc[page_no]
        rect = page.rect
        # Invisible text layer. The font is selected to support Devanagari where available.
        fontfile = None
        if "hin" in lang:
            candidates = [
                "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Regular.ttf",
            ]
            fontfile = next((p for p in candidates if os.path.exists(p)), None)
        if not fontfile:
            candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
            fontfile = next((p for p in candidates if os.path.exists(p)), None)
        box = fitz.Rect(8, 8, rect.width - 8, rect.height - 8)
        try:
            page.insert_textbox(
                box, text, fontfile=fontfile, fontsize=7,
                color=(0, 0, 0), render_mode=3, overlay=True,
                lineheight=1.15,
            )
        except Exception:
            # A searchable text layer is best-effort; preserve the visual PDF regardless.
            page.insert_text((10, rect.height - 10), text[:2000], fontsize=1, render_mode=3)
    return doc.tobytes(garbage=4, deflate=True)

def safe_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name.rsplit(".", 1)[0]).strip("-") or "document"
    return stem[:70]

# Upload and document state
left, right = st.columns([1.35, 0.65], gap="large")
with left:
    st.markdown('<div class="section-kicker">01 / Bring in a document</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("Choose a PDF to work on", type=["pdf"], help="POC limit: 10 MB and 10 pages. Password-protected PDFs are not supported.")
with right:
    st.markdown('<div class="section-kicker">02 / Runtime check</div>', unsafe_allow_html=True)
    ok, status = get_tesseract_status()
    st.markdown(f'<div class="paper-card"><div class="metric">{"Ready" if ok else "Needs setup"}</div><div class="metric-label">CPU OCR engine</div><div class="small-mono" style="margin-top:10px">{status}</div></div>', unsafe_allow_html=True)

if uploaded is None:
    st.markdown('<div class="note" style="margin-top:16px">Start with a PDF. The demo processes pages on the CPU, one at a time. Files are held in memory for this session and are not intentionally saved to permanent storage.</div>', unsafe_allow_html=True)
    st.markdown('<div class="footerline">Foliodesk · Local-first document handling · Demonstration build</div>', unsafe_allow_html=True)
    st.stop()

pdf_bytes = uploaded.getvalue()
if len(pdf_bytes) > MAX_BYTES:
    st.error("This POC accepts PDFs up to 10 MB. Please reduce the file size and try again.")
    st.stop()

file_key = hashlib.sha256(pdf_bytes).hexdigest()
if st.session_state.get("file_key") != file_key:
    try:
        probe = fitz.open(stream=pdf_bytes, filetype="pdf")
        if probe.needs_pass:
            st.error("This PDF is password-protected. Please upload an unlocked copy.")
            st.stop()
        if len(probe) > MAX_PAGES:
            st.error(f"This POC is limited to {MAX_PAGES} pages. Your file has {len(probe)} pages.")
            st.stop()
        if len(probe) == 0:
            st.error("The PDF has no pages.")
            st.stop()
    except Exception as exc:
        st.error(f"Could not read this PDF: {exc}")
        st.stop()
    st.session_state.clear()
    st.session_state["file_key"] = file_key
    st.session_state["filename"] = uploaded.name
    st.session_state["pdf_bytes"] = pdf_bytes
    st.session_state["page_count"] = len(probe)
    st.session_state["ocr_text"] = {}
    st.session_state["ocr_words"] = {}
    st.session_state["rotations"] = {}
    st.session_state["deleted_pages"] = set()
    st.session_state["added_text"] = {}
    st.session_state["ocr_language"] = "Hindi + English"
    st.session_state["ocr_complete"] = False
    probe.close()

doc = fitz.open(stream=pdf_bytes, filetype="pdf")
page_count = len(doc)
st.markdown(f"""
<div style="display:flex;align-items:center;gap:12px;margin:20px 0 8px">
  <span class="step-tag">OPEN DOCUMENT</span>
  <strong style="font-family:Manrope;font-size:16px">{uploaded.name}</strong>
  <span class="small-mono">{page_count} PAGES · {len(pdf_bytes)/1024:.0f} KB</span>
</div>
""", unsafe_allow_html=True)

tab_read, tab_edit, tab_export = st.tabs(["01  Read & correct", "02  Page workshop", "03  Export"])

with tab_read:
    st.markdown('<div class="section-kicker">Recognition desk / CPU-based OCR</div>', unsafe_allow_html=True)
    a, b = st.columns([0.72, 1.28], gap="large")
    with a:
        page_options = [f"Page {i+1}" for i in range(page_count)]
        selected_label = st.selectbox("Preview page", page_options, key="preview_page")
        preview_no = int(selected_label.split()[-1]) - 1
        st.image(render_page(doc, preview_no), use_container_width=True, caption=f"Original page {preview_no+1}")
    with b:
        language = st.selectbox(
            "Recognition language",
            ["Hindi + English", "English", "Hindi", "French", "German", "Spanish"],
            index=0,
            help="Hindi and English language data are included in the Render setup. Other languages require their Tesseract traineddata packages.",
        )
        scope = st.radio("Pages to recognise", ["All pages", "Preview page only"], horizontal=True)
        run_col, clear_col = st.columns([1, 1])
        with run_col:
            run_ocr = st.button("Run OCR on CPU", type="primary", use_container_width=True, disabled=not ok)
        with clear_col:
            if st.button("Clear recognised text", use_container_width=True):
                st.session_state["ocr_text"] = {}
                st.session_state["ocr_words"] = {}
                st.session_state["ocr_complete"] = False
                st.rerun()
        if not ok:
            st.warning("OCR is unavailable until Tesseract and its language packs are installed.")
        if run_ocr:
            target_pages = list(range(page_count)) if scope == "All pages" else [preview_no]
            progress = st.progress(0, text="Preparing page images…")
            for n, pno in enumerate(target_pages):
                try:
                    image = render_page(doc, pno, scale=1.65)
                    text_result, words = ocr_page(image, lang_code(language))
                    st.session_state["ocr_text"][pno] = text_result
                    st.session_state["ocr_words"][pno] = words
                    progress.progress((n + 1) / len(target_pages), text=f"Recognised page {pno+1} of {page_count}")
                except Exception as exc:
                    st.error(f"OCR failed on page {pno+1}: {exc}")
                    break
            st.session_state["ocr_language"] = language
            st.session_state["ocr_complete"] = True
            progress.empty()
            st.rerun()
        st.markdown('<div class="section-kicker">Recognised text / editable</div>', unsafe_allow_html=True)
        if preview_no in st.session_state["ocr_text"]:
            corrected = st.text_area(
                "Correct OCR mistakes here",
                value=st.session_state["ocr_text"][preview_no],
                height=330,
                key=f"edited_text_{preview_no}",
                label_visibility="collapsed",
                placeholder="Recognised text will appear here…",
            )
            st.session_state["ocr_text"][preview_no] = corrected
            st.caption(f"{len(corrected)} characters · Page {preview_no+1}")
            if st.button("Save correction for this page", key=f"save_{preview_no}"):
                st.success("Correction saved for this session.")
        else:
            st.text_area("Recognised text", value="", height=330, disabled=True, label_visibility="collapsed", placeholder="Run OCR to populate editable text for this page.")
    st.markdown('<div class="note">Recognition quality depends on scan clarity, typeface, skew and language data. Always review important names, numbers and legal/financial content before using the output.</div>', unsafe_allow_html=True)

with tab_edit:
    st.markdown('<div class="section-kicker">Page workshop / straightforward PDF edits</div>', unsafe_allow_html=True)
    e1, e2 = st.columns([1, 1], gap="large")
    with e1:
        st.markdown('<div class="paper-card"><strong>Rotate pages</strong><p class="small-mono">Choose a page and rotate it by 90° clockwise.</p></div>', unsafe_allow_html=True)
        rotate_page_label = st.selectbox("Page to rotate", [f"Page {i+1}" for i in range(page_count)], key="rotate_page")
        rotate_degrees = st.selectbox("Rotation", ["90° clockwise", "180°", "270° clockwise"], key="rotation_value")
        if st.button("Apply rotation", use_container_width=True):
            pno = int(rotate_page_label.split()[-1]) - 1
            delta = {"90° clockwise": 90, "180°": 180, "270° clockwise": 270}[rotate_degrees]
            st.session_state["rotations"][pno] = (st.session_state["rotations"].get(pno, 0) + delta) % 360
            st.success(f"Rotation queued for page {pno+1}.")
    with e2:
        st.markdown('<div class="paper-card"><strong>Remove a page</strong><p class="small-mono">Deleted pages are excluded from the exported PDF.</p></div>', unsafe_allow_html=True)
        delete_label = st.selectbox("Page to remove", [f"Page {i+1}" for i in range(page_count)], key="delete_page")
        if st.button("Mark page for removal", use_container_width=True):
            pno = int(delete_label.split()[-1]) - 1
            st.session_state["deleted_pages"].add(pno)
            st.success(f"Page {pno+1} marked for removal.")
        if st.session_state["deleted_pages"]:
            st.caption("Marked for removal: " + ", ".join(str(x+1) for x in sorted(st.session_state["deleted_pages"])))
            if st.button("Undo all page removals"):
                st.session_state["deleted_pages"] = set()
                st.rerun()
    st.markdown('<div class="section-kicker">Add a text note</div>', unsafe_allow_html=True)
    t1, t2 = st.columns([0.55, 1.45])
    with t1:
        add_page_label = st.selectbox("Place note on", [f"Page {i+1}" for i in range(page_count)], key="add_text_page")
        note_size = st.slider("Text size", 8, 28, 12)
    with t2:
        note_text = st.text_input("Text to add", placeholder="e.g. Reviewed copy · 25 Nov 2026")
    if st.button("Add note to page"):
        pno = int(add_page_label.split()[-1]) - 1
        st.session_state["added_text"][pno] = {"text": note_text, "size": note_size}
        st.success(f"Text note queued for page {pno+1}.")
    if st.session_state["rotations"] or st.session_state["deleted_pages"] or st.session_state["added_text"]:
        st.info(
            f"Pending edits · {len(st.session_state['rotations'])} rotated · "
            f"{len(st.session_state['deleted_pages'])} removed · {len(st.session_state['added_text'])} text notes"
        )
    st.caption("This POC supports rotation, page removal and adding a simple text note. It does not yet provide arbitrary click-and-replace editing of existing PDF text.")

with tab_export:
    st.markdown('<div class="section-kicker">Export bench / prepare your files</div>', unsafe_allow_html=True)
    st.markdown("Choose an output. Page edits are applied to the exported PDF; OCR text corrections are included in the searchable PDF.")
    out_name = safe_filename(uploaded.name)
    text_parts = []
    for pno in range(page_count):
        if pno in st.session_state["ocr_text"]:
            text_parts.append(f"--- PAGE {pno+1} ---\n{st.session_state['ocr_text'][pno]}")
    if text_parts:
        st.download_button("Download recognised text (.txt)", "\n\n".join(text_parts).encode("utf-8"), file_name=f"{out_name}-ocr.txt", mime="text/plain", use_container_width=True)
    else:
        st.button("Download recognised text (.txt)", disabled=True, use_container_width=True)
        st.caption("Run OCR on at least one page to enable the text download.")

    def build_edited_pdf(include_ocr: bool = False) -> bytes:
        out = fitz.open(stream=pdf_bytes, filetype="pdf")
        # Apply rotation and notes before removing pages, preserving original page indexes.
        for pno, rotation in st.session_state["rotations"].items():
            if pno < len(out):
                out[pno].set_rotation(rotation)
        for pno, note in st.session_state["added_text"].items():
            if pno < len(out) and note["text"].strip():
                page = out[pno]
                page.insert_text(
                    fitz.Point(36, 36 + int(note["size"])),
                    note["text"], fontsize=int(note["size"]), color=(0.25, 0.20, 0.42),
                    fontname="helv", overlay=True,
                )
        for pno in sorted(st.session_state["deleted_pages"], reverse=True):
            if 0 <= pno < len(out) and len(out) > 1:
                out.delete_page(pno)
        if include_ocr and st.session_state["ocr_text"]:
            # Add corrected text layers using original page numbering; map around deleted pages.
            remaining_original = [i for i in range(page_count) if i not in st.session_state["deleted_pages"]]
            for new_idx, original_idx in enumerate(remaining_original):
                if new_idx < len(out) and original_idx in st.session_state["ocr_text"]:
                    text = st.session_state["ocr_text"][original_idx]
                    if text.strip():
                        page = out[new_idx]
                        fontfile = None
                        if "Hindi" in st.session_state.get("ocr_language", ""):
                            for candidate in (
                                "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                                "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Regular.ttf",
                            ):
                                if os.path.exists(candidate):
                                    fontfile = candidate
                                    break
                        if not fontfile and os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
                            fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                        try:
                            page.insert_textbox(
                                fitz.Rect(8, 8, page.rect.width-8, page.rect.height-8),
                                text, fontfile=fontfile, fontsize=7, render_mode=3,
                                lineheight=1.15, overlay=True,
                            )
                        except Exception:
                            pass
        return out.tobytes(garbage=4, deflate=True)

    c1, c2 = st.columns(2, gap="large")
    with c1:
        try:
            edited_bytes = build_edited_pdf(False)
            st.download_button("Download edited PDF", edited_bytes, file_name=f"{out_name}-edited.pdf", mime="application/pdf", use_container_width=True)
        except Exception as exc:
            st.error(f"Could not prepare edited PDF: {exc}")
    with c2:
        if st.session_state["ocr_text"]:
            try:
                searchable_bytes = build_edited_pdf(True)
                st.download_button("Download searchable PDF", searchable_bytes, file_name=f"{out_name}-searchable.pdf", mime="application/pdf", use_container_width=True)
            except Exception as exc:
                st.error(f"Could not prepare searchable PDF: {exc}")
        else:
            st.button("Download searchable PDF", disabled=True, use_container_width=True)
            st.caption("Recognise at least one page first.")

    st.markdown('<div class="note">The searchable PDF keeps the original page artwork and adds an invisible text layer. On this POC, corrected text is placed as a page-level text layer; complex layouts may not align perfectly with the original scan.</div>', unsafe_allow_html=True)

doc.close()
st.markdown('<div class="footerline">Foliodesk · CPU OCR · Hindi / English starter build · Review output before production use</div>', unsafe_allow_html=True)
