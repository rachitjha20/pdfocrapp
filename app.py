"""FolioDesk: make scanned PDFs searchable, correct the text, tidy the pages.

This file is the Streamlit interface only. All document work lives in processing.py.
"""
from __future__ import annotations

import hashlib
import html
from pathlib import Path

import streamlit as st
from PIL import ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates

import processing as P

SAMPLE = Path(__file__).parent / "samples" / "sample-scan.pdf"
PREVIEW_DPI = 110
THUMB_DPI = 45

st.set_page_config(page_title="FolioDesk", page_icon="📄", layout="wide", initial_sidebar_state="collapsed")

# A scanner bed: cool grey desk, white sheets, stamp-pad blue for actions and
# highlighter yellow reserved for one job, marking words that need a second look.
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Mukta:wght@400;500;600;700;800&display=swap');
:root { --desk:#E8ECF1; --sheet:#FFFFFF; --ink:#16202C; --muted:#566373; --line:#CFD7E1;
        --stamp:#1F3FBF; --marker:#FFD60A; --strike:#B93520; }
html, body, .stApp, .stMarkdown, button, input, textarea, label, p, li, h1, h2, h3, h4 {
  font-family:'Mukta','Noto Sans Devanagari',system-ui,sans-serif; }
.stApp { background:var(--desk); color:var(--ink); }
[data-testid="stMainBlockContainer"] { max-width:1320px; padding-top:1.6rem; padding-bottom:3rem; }
header[data-testid="stHeader"] { background:transparent; }
#MainMenu, footer { visibility:hidden; }

.fd-top { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; padding-bottom:12px;
          border-bottom:2px solid var(--ink); margin-bottom:22px; }
.fd-brand { font-weight:800; font-size:22px; letter-spacing:-.4px; }
.fd-file { font-weight:600; font-size:17px; overflow-wrap:anywhere; }
.fd-meta { color:var(--muted); font-size:15px; }
.fd-h1 { font-weight:800; font-size:clamp(34px,4.6vw,58px); line-height:1.02; letter-spacing:-1.6px;
         margin:18px 0 14px; max-width:13ch; }
.fd-lede { font-size:18px; line-height:1.55; color:#2B3644; max-width:52ch; margin-bottom:22px; }
.fd-h2 { font-weight:700; font-size:20px; margin:4px 0 2px; }
.fd-help { color:var(--muted); font-size:15px; line-height:1.5; margin:0 0 12px; max-width:70ch; }
.fd-status { font-size:15.5px; line-height:1.5; padding:9px 12px; background:var(--sheet);
             border:1px solid var(--line); border-left:4px solid var(--stamp); border-radius:4px; margin-bottom:10px; }
.fd-status.warn { border-left-color:var(--strike); }
.fd-status mark { background:var(--marker); color:var(--ink); padding:0 4px; border-radius:2px; }
.fd-cap { color:var(--muted); font-size:14.5px; margin-top:2px; }
.fd-cap.removed { color:var(--strike); font-weight:600; }
.fd-list { margin:0; padding-left:18px; font-size:16px; line-height:1.7; }

[data-testid="stImage"] img { background:var(--sheet); border:1px solid var(--line); border-radius:2px;
  box-shadow:0 1px 0 #fff inset, 0 10px 24px -14px rgba(22,32,44,.45); }
[data-testid="stFileUploader"] section { background:var(--sheet); border:1.5px dashed #9AA7B7; border-radius:8px; }
[data-testid="stFileUploader"] section:hover { border-color:var(--stamp); }
.stButton > button, .stDownloadButton > button { border-radius:6px; font-weight:600; font-size:16px; min-height:42px; }
.stTextArea textarea { font-size:17px; line-height:1.65; background:var(--sheet); color:var(--ink); }
.stTabs [data-baseweb="tab-list"] { gap:4px; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { font-weight:600; font-size:17px; padding:10px 16px; color:var(--muted); }
.stTabs [aria-selected="true"] { color:var(--ink); }
:focus-visible { outline:3px solid var(--stamp); outline-offset:2px; }
@media (max-width:640px) { .fd-h1 { letter-spacing:-1px; } [data-testid="stMainBlockContainer"] { padding-top:1rem; } }
</style>
""",
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Cached helpers
# --------------------------------------------------------------------------

@st.cache_data(show_spinner=False, ttl=300)
def engine() -> dict:
    return P.tesseract_status()


@st.cache_data(show_spinner=False, max_entries=96)
def page_image(file_key: str, page_no: int, dpi: int, rotation: int, _pdf_bytes: bytes, edits: list | None = None):
    return P.render_page(_pdf_bytes, page_no, dpi, rotation, edits)


@st.cache_data(show_spinner=False, max_entries=64)
def page_size(file_key: str, page_no: int, rotation: int, _pdf_bytes: bytes) -> tuple[float, float]:
    image = P.render_page(_pdf_bytes, page_no, 72, rotation)
    return float(image.width), float(image.height)


@st.cache_data(show_spinner=False, max_entries=8)
def export_pdf(file_key: str, rotations: dict, removed: tuple, notes: list, results: dict, texts: dict,
               text_layer: bool, order: list, edits: dict, options: dict, _pdf_bytes: bytes):
    return P.build_pdf(_pdf_bytes, rotations, removed, notes, results, texts, text_layer,
                       order=order, edits=edits, **options)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

def load_document(name: str, pdf_bytes: bytes) -> None:
    """Validate a file and make it the open document. Records the reason if it can't be used."""
    try:
        pages = P.inspect_pdf(pdf_bytes)
    except P.PdfLocked as exc:
        st.session_state["locked"] = (name, pdf_bytes)
        st.session_state["load_error"] = str(exc)
        return
    except P.PdfError as exc:
        st.session_state["load_error"] = str(exc)
        return
    uploads = st.session_state.get("uploads", 0)
    st.session_state.clear()
    # Pages are known by their index in pdf_bytes; `order` is how they are arranged.
    st.session_state.update(
        uploads=uploads, file_key=hashlib.sha256(pdf_bytes).hexdigest(), filename=name, pdf_bytes=pdf_bytes,
        order=list(range(pages)), current=0, edit_page=0, draw=0,
        results={},    # page -> recognition result
        texts={},      # page -> text as corrected by the user
        rotations={},  # page -> extra clockwise degrees
        edits={},      # page -> list of edits drawn on it
        removed=set(), notes=[], notices=[],
    )


def unlock_document(password: str) -> None:
    name, locked = st.session_state["locked"]
    try:
        opened = P.unlock_pdf(locked, password)
    except P.PdfError as exc:
        st.session_state["load_error"] = str(exc)
        return
    del st.session_state["locked"]
    load_document(name, opened)


def add_pages(extra: bytes) -> None:
    s = st.session_state
    try:
        try:
            merged, added = P.merge_pdfs(s["pdf_bytes"], extra)
        except P.PdfLocked:
            raise P.PdfError("That PDF is password-protected. Open it on its own first, export an unlocked copy, then add it.")
    except P.PdfError as exc:
        s["notices"].append(str(exc))
        return
    first_new = max(s["order"]) + 1
    s["pdf_bytes"] = merged
    s["file_key"] = hashlib.sha256(merged).hexdigest()
    s["order"].extend(range(first_new, first_new + added))
    s["notices"].append(f"Added {plural(added, 'page')} at the end")


def position(page_no: int) -> int:
    """The page's number as the user sees it: its place in the current arrangement."""
    return st.session_state["order"].index(page_no) + 1


def move(page_no: int, step: int) -> None:
    order = st.session_state["order"]
    i = order.index(page_no)
    j = i + step
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]


def close_document() -> None:
    uploads = st.session_state.get("uploads", 0) + 1
    st.session_state.clear()
    st.session_state["uploads"] = uploads


def set_text(page_no: int, text: str) -> None:
    st.session_state["texts"][page_no] = text
    st.session_state[f"text_{page_no}"] = text


def keep_edit(page_no: int) -> None:
    st.session_state["texts"][page_no] = st.session_state[f"text_{page_no}"]


def is_edited(page_no: int) -> bool:
    s = st.session_state
    return page_no in s["results"] and s["texts"].get(page_no) != s["results"][page_no]["text"]


def is_stale(page_no: int) -> bool:
    """True when the page was rotated after it was recognised."""
    s = st.session_state
    result = s["results"].get(page_no)
    return bool(result) and result["rotation"] != s["rotations"].get(page_no, 0)


def rotate(page_no: int, degrees: int) -> None:
    rotations = st.session_state["rotations"]
    rotations[page_no] = (rotations.get(page_no, 0) + degrees) % 360


def toggle_removed(page_no: int) -> None:
    st.session_state["removed"] ^= {page_no}


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# --------------------------------------------------------------------------
# Landing
# --------------------------------------------------------------------------

def landing() -> None:
    status = engine()
    st.markdown('<div class="fd-top"><span class="fd-brand">FolioDesk</span></div>', unsafe_allow_html=True)
    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        st.markdown(
            '<h1 class="fd-h1">Make a scanned PDF searchable.</h1>'
            '<p class="fd-lede">Upload a scan in English or Hindi. FolioDesk reads every page, marks the words '
            "it is unsure about so you can correct them, and gives you back a PDF you can search and copy from.</p>",
            unsafe_allow_html=True,
        )
        if error := st.session_state.pop("load_error", None):
            st.error(error)
        if "locked" in st.session_state:
            with st.form("unlock", border=False):
                password = st.text_input(f"Password for {st.session_state['locked'][0]}", type="password")
                go, cancel = st.columns(2)
                if go.form_submit_button("Open file", type="primary", use_container_width=True):
                    unlock_document(password)
                    st.rerun()
                if cancel.form_submit_button("Choose another file", use_container_width=True):
                    del st.session_state["locked"]
                    st.session_state["uploads"] = st.session_state.get("uploads", 0) + 1
                    st.rerun()
            return
        uploaded = st.file_uploader(
            f"Choose a PDF (up to {P.MAX_BYTES // 1_048_576} MB and {P.MAX_PAGES} pages)",
            type=["pdf"], key=f"upload_{st.session_state.get('uploads', 0)}",
        )
        if uploaded is not None:
            load_document(uploaded.name, uploaded.getvalue())
            if "load_error" in st.session_state and "locked" not in st.session_state:
                # A fresh uploader, so the rejected file is not offered again.
                st.session_state["uploads"] = st.session_state.get("uploads", 0) + 1
            st.rerun()
        st.markdown(
            '<p class="fd-help">Your file is held in memory for this session and is gone when you close it. '
            "This is a demo, so avoid confidential documents.</p>",
            unsafe_allow_html=True,
        )
        if not status["ok"]:
            st.warning(f"Text recognition is unavailable: {status['detail']} You can still rotate, remove and annotate pages.")
    with right:
        if SAMPLE.exists():
            sample = SAMPLE.read_bytes()
            st.image(page_image("sample", 0, 80, 0, sample), use_container_width=True)
            st.markdown(
                '<p class="fd-cap">No scan to hand? The sample is a two-page housing society notice in English '
                "and Hindi, with one page scanned sideways.</p>",
                unsafe_allow_html=True,
            )
            if st.button("Open the sample scan", use_container_width=True):
                load_document(SAMPLE.name, sample)
                st.rerun()


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------

def top_bar() -> None:
    s = st.session_state
    done = len(s["results"])
    meta = f"{plural(len(s['order']), 'page')}, {len(s['pdf_bytes']) / 1024:.0f} KB"
    if done:
        meta += f", {done} read"
    bar, action = st.columns([5, 1], vertical_alignment="bottom")
    with bar:
        st.markdown(
            f'<div class="fd-top" style="margin-bottom:0;border-bottom:0;padding-bottom:0">'
            f'<span class="fd-brand">FolioDesk</span>'
            f'<span class="fd-file">{html.escape(s["filename"])}</span>'
            f'<span class="fd-meta">{meta}</span></div>',
            unsafe_allow_html=True,
        )
    with action:
        st.button("Close file", on_click=close_document, use_container_width=True)
    st.markdown('<div class="fd-top" style="padding:0;margin:6px 0 10px"></div>', unsafe_allow_html=True)
    for notice in s["notices"]:
        st.toast(notice)
    s["notices"] = []


def run_recognition(pages: list[int], language: str, detail: str, straighten: bool, use_embedded: bool) -> None:
    s = st.session_state
    total = len(pages)
    bar = st.progress(0.0, text=f"Reading {plural(total, 'page')}…")
    finished = 0

    def on_done(page_no, result, exc):
        nonlocal finished
        finished += 1
        bar.progress(finished / total, text=f"Read page {position(page_no)} ({finished} of {total})")

    before = dict(s["rotations"])
    results, errors = P.recognise_pages(
        s["pdf_bytes"], pages, P.LANGUAGES[language], P.QUALITY_DPI[detail],
        rotations=before, use_embedded=use_embedded, auto_rotate=straighten, on_done=on_done,
    )
    bar.empty()
    turned = []
    for page_no, result in results.items():
        s["results"][page_no] = result
        set_text(page_no, result["text"])
        if result["rotation"] != before.get(page_no, 0):
            s["rotations"][page_no] = result["rotation"]
            turned.append(position(page_no))
    if turned:
        s["notices"].append("Turned upright: page " + ", ".join(map(str, sorted(turned))))
    for page_no, message in sorted(errors.items()):
        s["notices"].append(f"Page {position(page_no)} could not be read. {message}")
    if results and not errors:
        seconds = sum(r["seconds"] for r in results.values())
        s["notices"].append(f"Read {plural(len(results), 'page')} in {seconds:.1f} s of processing")


def read_tab() -> None:
    s = st.session_state
    status = engine()
    order = s["order"]
    languages = P.available_languages(status["langs"])
    can_read = status["ok"] and bool(languages)

    if not can_read:
        st.warning(
            "Text recognition is unavailable here. "
            + (status["detail"] if not status["ok"] else "No supported Tesseract language data is installed.")
        )

    c_lang, c_detail, c_opts, c_all, c_one = st.columns([1.5, 1.2, 1, 1.5, 1.2], vertical_alignment="bottom")
    with c_lang:
        language = st.selectbox("Language of the document", languages or ["English"], disabled=not can_read)
    with c_detail:
        detail = st.selectbox(
            "Scan detail", list(P.QUALITY_DPI), index=1, disabled=not can_read,
            help="Fast suits clean, large print. Fine print reads small or faint text more carefully and takes longer.",
        )
    with c_opts:
        with st.popover("Options", use_container_width=True):
            straighten = st.toggle(
                "Turn sideways pages upright", value="osd" in status["langs"], disabled="osd" not in status["langs"],
                help="Checks each page's orientation before reading it.",
            )
            use_embedded = st.toggle(
                "Reuse text the PDF already has", value=True,
                help="Pages that already contain real text are copied exactly instead of being recognised from the image.",
            )
            show_marks = st.toggle("Mark uncertain words on the page", value=True)

    if s["current"] not in order:
        s["current"] = order[0]

    def page_label(p: int) -> str:
        suffix = " (removed)" if p in s["removed"] else " ✓" if p in s["results"] else ""
        return f"{position(p)}{suffix}"

    todo = [p for p in order if p not in s["removed"] and not is_edited(p)]
    with c_all:
        read_all = st.button("Read all pages", type="primary", use_container_width=True, disabled=not (can_read and todo))
    with c_one:
        read_one = st.button("Read this page", use_container_width=True, disabled=not can_read)

    if len(order) > 1:
        picked = st.segmented_control(
            "Page", order, format_func=page_label, default=s["current"], key="page_pick",
        )
        if picked is not None:
            s["current"] = picked
    page_no = s["current"]

    if read_all or read_one:
        kept = [position(p) for p in order if read_all and is_edited(p) and p not in s["removed"]]
        run_recognition(todo if read_all else [page_no], language, detail, straighten, use_embedded)
        if kept:
            s["notices"].append("Kept your corrections on page " + ", ".join(map(str, kept)))
        st.rerun()

    result = s["results"].get(page_no)
    rotation = s["rotations"].get(page_no, 0)
    left, right = st.columns([0.9, 1.1], gap="large")
    with left:
        image = page_image(s["file_key"], page_no, PREVIEW_DPI, rotation, s["pdf_bytes"], s["edits"].get(page_no))
        if result and show_marks and not is_stale(page_no) and result["low_count"] > 0:
            image = P.highlight_words(image, result["size"], result["words"])
        st.image(image, use_container_width=True)
    with right:
        if result is None:
            st.markdown(
                '<div class="fd-status">This page has not been read yet. Choose the language, then '
                "<b>Read all pages</b>.</div>",
                unsafe_allow_html=True,
            )
            st.text_area("Text on this page", "", height=460, disabled=True,
                         placeholder="The page's text appears here, ready to correct.")
            return
        if is_stale(page_no):
            st.markdown(
                '<div class="fd-status warn">This page was rotated after it was read. Read it again so the '
                "searchable text lines up with the page.</div>",
                unsafe_allow_html=True,
            )
        elif result["source"] == "embedded":
            st.markdown(
                '<div class="fd-status">This page already contained text, so it was copied exactly rather than '
                "recognised from the image.</div>",
                unsafe_allow_html=True,
            )
        elif not result["words"]:
            st.markdown('<div class="fd-status warn">No text was found on this page.</div>', unsafe_allow_html=True)
        else:
            low = result["low_count"]
            review = (
                f"<mark>{plural(low, 'word')}</mark> marked on the page for a second look."
                if low else "No words needed marking."
            )
            st.markdown(
                f'<div class="fd-status">Read with {result["confidence"]:.0f}% average confidence. {review}</div>',
                unsafe_allow_html=True,
            )
        key = f"text_{page_no}"
        if key not in s:
            s[key] = s["texts"].get(page_no, result["text"])
        st.text_area("Text on this page", key=key, height=460, on_change=keep_edit, args=(page_no,),
                     help="Corrections you make here go into the text file and the searchable PDF.")
        text = s["texts"].get(page_no, "")
        info, undo = st.columns([2, 1], vertical_alignment="center")
        with info:
            st.markdown(
                f'<p class="fd-cap">{plural(len(text.split()), "word")}'
                f'{", corrected by you" if is_edited(page_no) else ""}</p>',
                unsafe_allow_html=True,
            )
        with undo:
            if is_edited(page_no):
                st.button("Undo my corrections", on_click=set_text, args=(page_no, result["text"]),
                          use_container_width=True)


def pages_tab() -> None:
    s = st.session_state
    order = s["order"]
    count = len(order)
    st.markdown(
        '<p class="fd-help">Put pages in order, turn them the right way up and drop the ones you do not need. '
        "Nothing changes in your original file; the edits apply to what you export.</p>",
        unsafe_allow_html=True,
    )
    kept = count - len(s["removed"])
    per_row = 5
    for start in range(0, count, per_row):
        cols = st.columns(per_row, gap="medium")
        for col, page_no in zip(cols, list(order[start:start + per_row])):
            with col:
                place = position(page_no)
                rotation = s["rotations"].get(page_no, 0)
                gone = page_no in s["removed"]
                thumb = page_image(s["file_key"], page_no, THUMB_DPI, rotation, s["pdf_bytes"], s["edits"].get(page_no))
                st.image(P.dim_image(thumb) if gone else thumb, use_container_width=True)
                if gone:
                    st.markdown(f'<p class="fd-cap removed">Page {place}, removed</p>', unsafe_allow_html=True)
                else:
                    turned = f", turned {rotation}°" if rotation else ""
                    st.markdown(f'<p class="fd-cap">Page {place}{turned}</p>', unsafe_allow_html=True)
                m1, m2, b1, b2 = st.columns(4)
                m1.button(":material/arrow_back:", key=f"e{page_no}", help="Move earlier", disabled=place == 1,
                          on_click=move, args=(page_no, -1), use_container_width=True)
                m2.button(":material/arrow_forward:", key=f"f{page_no}", help="Move later", disabled=place == count,
                          on_click=move, args=(page_no, 1), use_container_width=True)
                b1.button(":material/rotate_left:", key=f"l{page_no}", help="Turn left", disabled=gone,
                          on_click=rotate, args=(page_no, 270), use_container_width=True)
                b2.button(":material/rotate_right:", key=f"r{page_no}", help="Turn right", disabled=gone,
                          on_click=rotate, args=(page_no, 90), use_container_width=True)
                st.button("Restore" if gone else "Remove", key=f"x{page_no}", on_click=toggle_removed,
                          args=(page_no,), use_container_width=True, disabled=not gone and kept <= 1,
                          help="The last remaining page cannot be removed." if not gone and kept <= 1 else None)

    room = P.MAX_PAGES - count
    st.markdown('<h2 class="fd-h2" style="margin-top:26px">Add pages from another PDF</h2>', unsafe_allow_html=True)
    if room <= 0:
        st.markdown(f'<p class="fd-help">This document is at the {P.MAX_PAGES}-page limit.</p>', unsafe_allow_html=True)
    else:
        extra = st.file_uploader(f"The pages go at the end; move them from there. Room for {plural(room, 'more page')}.",
                                 type=["pdf"], key=f"merge_{s['uploads']}_{len(s['pdf_bytes'])}")
        if extra is not None:
            add_pages(extra.getvalue())
            s["uploads"] += 1
            st.rerun()

    st.markdown('<h2 class="fd-h2" style="margin-top:26px">Stamp a note on a page</h2>'
                '<p class="fd-help">A short visible line such as “Reviewed copy” or a reference number. '
                "Hindi works too.</p>", unsafe_allow_html=True)
    with st.form("note", clear_on_submit=True, border=False):
        n_text, n_page, n_pos, n_size, n_add = st.columns([3, 1, 1.4, 1, 1.1], vertical_alignment="bottom")
        text = n_text.text_input("Note", placeholder="Reviewed copy, 25 Nov 2026", max_chars=120)
        target = n_page.selectbox("Page", order, format_func=lambda p: str(position(p)))
        corner = n_pos.selectbox("Corner", P.NOTE_POSITIONS)
        size = n_size.number_input("Size", 8, 28, 12)
        if n_add.form_submit_button("Add note", use_container_width=True):
            if text.strip():
                s["notes"].append({"page": target, "text": text.strip(), "position": corner, "size": int(size)})
            else:
                st.warning("Type the note's text first.")
    for i, note in enumerate(s["notes"]):
        line, drop = st.columns([6, 1], vertical_alignment="center")
        line.markdown(
            f'<div class="fd-status" style="margin:0">Page {position(note["page"])}, {note["position"].lower()}: '
            f'{html.escape(note["text"])}</div>', unsafe_allow_html=True)
        if drop.button("Delete", key=f"note_{i}", use_container_width=True):
            s["notes"].pop(i)
            st.rerun()


TOOLS = {
    "Add text": "text", "Highlight": "highlight", "Cover up": "cover",
    "Redact": "redact", "Add image": "image", "Replace text": "replace",
}
TOOL_HELP = {
    "text": "Drag a box on the page where the text should go, then type it.",
    "highlight": "Drag across the part to highlight, or type the words to find.",
    "cover": "Drag over the part to hide under a block of colour. What is underneath stays in the file.",
    "redact": "Drag over the part to black out, or type the words to find. Redaction deletes what is "
              "underneath from the file for good, including from a scan's image.",
    "image": "Drag a box where the picture should go, then choose it. Useful for a signature, stamp or logo.",
    "replace": "Type the words to change and what they should say. Works best on short pieces such as a "
               "number, date or name.",
}


def describe(edit: dict) -> str:
    kind = edit["kind"]
    if kind == "text":
        return f"Text: {edit['text'][:50]}"
    if kind == "replace":
        return f"Replaced with: {edit['text'][:50]}" if edit["text"] else "Text removed"
    return {"highlight": "Highlight", "cover": "Covered area", "redact": "Redaction", "image": "Image"}[kind]


def edit_tab() -> None:
    s = st.session_state
    pages = [p for p in s["order"] if p not in s["removed"]]
    if s["edit_page"] not in pages:
        s["edit_page"] = pages[0]
    if len(pages) > 1:
        picked = st.segmented_control("Page", pages, format_func=lambda p: str(position(p)),
                                      default=s["edit_page"], key="edit_pick")
        if picked is not None:
            s["edit_page"] = picked
    page_no = s["edit_page"]
    rotation = s["rotations"].get(page_no, 0)
    edits = s["edits"].setdefault(page_no, [])
    width, height = page_size(s["file_key"], page_no, rotation, s["pdf_bytes"])
    draw_key = f"draw_{page_no}_{s['draw']}"

    # The area last dragged on the page, in PDF points.
    area = None
    drag = s.get(draw_key)
    if drag and drag.get("width"):
        k = width / drag["width"]
        x0, x1 = sorted((drag["x1"] * k, drag["x2"] * k))
        y0, y1 = sorted((drag["y1"] * k, drag["y2"] * k))
        x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(width, x1), min(height, y1)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            area = [x0, y0, x1, y1]

    left, right = st.columns([1, 0.85], gap="large")
    with right:
        tool_name = st.radio("Tool", list(TOOLS), horizontal=True, key="tool")
        tool = TOOLS[tool_name]
        st.markdown(f'<p class="fd-help">{TOOL_HELP[tool]}</p>', unsafe_allow_html=True)
        result = s["results"].get(page_no)
        if result and is_stale(page_no):
            result = None
        new: list[dict] = []
        problem = ""
        uses_area = tool in ("text", "cover", "image")
        query = ""
        if tool == "text":
            text = st.text_area("Text", height=110, key=f"tool_text_{s['draw']}", placeholder="Approved for payment")
            c_size, c_color = st.columns(2)
            size = c_size.number_input("Size", 6, 72, 12, key="tool_size")
            color = c_color.color_picker("Colour", "#16202C", key="tool_ink")
            if area and text.strip():
                new = [{"kind": "text", "rect": area, "text": text.strip(), "size": int(size), "color": color}]
            elif not text.strip():
                problem = "Type the text to add."
        elif tool == "cover":
            color = st.color_picker("Colour", "#FFFFFF", key="tool_cover")
            if area:
                new = [{"kind": "cover", "rect": area, "color": color}]
        elif tool == "image":
            picture = st.file_uploader("Picture (PNG or JPEG)", type=["png", "jpg", "jpeg"], key=f"tool_image_{s['draw']}")
            if area and picture is not None:
                new = [{"kind": "image", "rect": area, "data": picture.getvalue()}]
            elif picture is None:
                problem = "Choose a picture."
        else:
            query = st.text_input("Words to find on this page", key=f"tool_find_{tool}_{s['draw']}",
                                  placeholder="Sahyog Housing Society" if tool != "replace" else "12 November 2026")
            matches = P.find_text(s["pdf_bytes"], page_no, query, rotation, result) if query.strip() else []
            if tool == "replace":
                swap = st.text_input("Change to", key=f"tool_swap_{s['draw']}", placeholder="14 November 2026")
                new = [{"kind": "replace", "text": swap.strip(), **m} for m in matches]
            else:
                color = st.color_picker("Colour", "#FFD60A", key="tool_marker") if tool == "highlight" else None
                targets = [m["rect"] for m in matches] if query.strip() else ([area] if area else [])
                new = [{"kind": tool, "rect": r, **({"color": color} if color else {})} for r in targets]
            if query.strip():
                found = plural(len(matches), "match") if len(matches) == 1 else f"{len(matches)} matches"
                st.markdown(f'<p class="fd-cap">{found} on this page.</p>', unsafe_allow_html=True)
                if not matches:
                    problem = ("Those words were not found. Check the spelling, or read the page first if it is a scan."
                               if engine()["ok"] else "Those words were not found on this page.")
            elif tool == "replace":
                problem = "Type the words to change."
        if not new and not problem:
            problem = "Drag on the page to mark the area." if (uses_area or not query.strip()) else ""
        go, clear = st.columns([2, 1])
        if go.button("Apply to page", type="primary", use_container_width=True, disabled=not new, help=problem or None):
            edits.extend({**e, "rotation": rotation} for e in new)
            s["draw"] += 1
            st.rerun()
        if clear.button("Clear selection", use_container_width=True, disabled=area is None):
            s["draw"] += 1
            st.rerun()
        if problem and not new:
            st.markdown(f'<p class="fd-cap">{problem}</p>', unsafe_allow_html=True)

        if edits:
            st.markdown(f'<h2 class="fd-h2" style="margin-top:22px">Changes on page {position(page_no)}</h2>',
                        unsafe_allow_html=True)
            for i, edit in enumerate(edits):
                line, drop = st.columns([4, 1], vertical_alignment="center")
                line.markdown(f'<div class="fd-status" style="margin:0">{html.escape(describe(edit))}</div>',
                              unsafe_allow_html=True)
                if drop.button("Undo", key=f"undo_{page_no}_{i}", use_container_width=True):
                    edits.pop(i)
                    st.rerun()
            if any(e["kind"] == "redact" for e in edits) and s["texts"].get(page_no, "").strip():
                st.markdown(
                    '<div class="fd-status warn" style="margin-top:10px">This page\'s recognised text may still contain '
                    "the redacted words. Delete them under Read and correct before downloading the text file. "
                    "The searchable layer is left off redacted pages.</div>", unsafe_allow_html=True)
    with left:
        image = page_image(s["file_key"], page_no, PREVIEW_DPI, rotation, s["pdf_bytes"], edits)
        if area:
            image = image.copy()
            k = image.width / width
            ImageDraw.Draw(image).rectangle([v * k for v in area], outline=(31, 63, 191), width=3)
        streamlit_image_coordinates(image, key=draw_key, click_and_drag=True, use_column_width="always",
                                    cursor="crosshair", image_format="JPEG", jpeg_quality=88)


def export_tab() -> None:
    s = st.session_state
    order = s["order"]
    kept = [p for p in order if p not in s["removed"]]
    places = lambda found: ", ".join(str(position(p)) for p in found)  # noqa: E731
    redacted = [p for p in kept if any(e["kind"] == "redact" for e in s["edits"].get(p, []))]
    layer_pages = [p for p in kept if p in s["results"] and s["results"][p]["source"] == "ocr" and not is_stale(p)]
    searchable = [p for p in layer_pages if p not in redacted]
    stale = [p for p in kept if is_stale(p)]
    unread = [p for p in kept if p not in s["results"]]
    name = P.safe_filename(s["filename"])

    left, right = st.columns([1.1, 0.9], gap="large")
    with left:
        st.markdown('<h2 class="fd-h2">Finishing touches</h2>', unsafe_allow_html=True)
        o1, o2 = st.columns(2)
        numbers = o1.checkbox("Number the pages", key="opt_numbers", help="Adds “1 / 5” at the foot of each page.")
        compress = o2.checkbox("Make the file smaller", key="opt_compress",
                               help="Stores scanned images at a lower resolution. Fine for reading on screen; "
                                    "keep it off if the PDF will be printed large.")
        watermark = st.text_input("Watermark across every page", key="opt_watermark", max_chars=40,
                                  placeholder="Leave empty for none, or type e.g. DRAFT")
        password = st.text_input("Password to open the PDF", key="opt_password", type="password", max_chars=64,
                                 placeholder="Leave empty for none",
                                 help="Anyone opening the downloaded PDF will need this. It is not stored.")

        st.markdown('<h2 class="fd-h2" style="margin-top:20px">What your PDF will contain</h2>', unsafe_allow_html=True)
        turned = sum(1 for p in kept if s["rotations"].get(p, 0))
        noted = sum(1 for n in s["notes"] if n["page"] in kept)
        changed = sum(len(s["edits"].get(p, [])) for p in kept)
        items = [f"{plural(len(kept), 'page')}" + (f" ({len(s['removed'])} removed)" if s["removed"] else "")]
        if kept != sorted(kept):
            items.append("Pages in your new order")
        if turned:
            items.append(f"{plural(turned, 'page')} turned")
        if changed:
            items.append(f"{plural(changed, 'edit')} from the Edit tab")
        if noted:
            items.append(f"{plural(noted, 'note')} stamped")
        if searchable:
            items.append(f"Searchable text on {plural(len(searchable), 'page')}, including your corrections")
        if watermark.strip():
            items.append(f"Watermark “{html.escape(watermark.strip())}”")
        if numbers:
            items.append("Page numbers")
        if password:
            items.append("Password needed to open it")
        st.markdown('<ul class="fd-list">' + "".join(f"<li>{i}</li>" for i in items) + "</ul>", unsafe_allow_html=True)
        warnings = []
        if unread and engine()["ok"]:
            warnings.append(f"Not read yet: page {places(unread)}. Those pages will not be searchable.")
        if stale:
            warnings.append(f"Rotated since reading: page {places(stale)}. Read them again to make them searchable.")
        if [p for p in redacted if p in layer_pages]:
            warnings.append(f"Page {places(p for p in redacted if p in layer_pages)} has redactions, so it gets no "
                            "searchable text. This keeps redacted words from surviving in a hidden layer.")
        for warning in warnings:
            st.markdown(f'<div class="fd-status warn" style="margin-top:12px">{warning}</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<h2 class="fd-h2">Download</h2>', unsafe_allow_html=True)
        options = {"watermark": watermark, "page_numbers": numbers, "compress": compress}
        args = (
            s["file_key"], dict(s["rotations"]), tuple(sorted(s["removed"])), list(s["notes"]),
            {p: s["results"][p] for p in layer_pages}, {p: s["texts"][p] for p in layer_pages if p in s["texts"]},
            bool(layer_pages), list(order), {p: list(e) for p, e in s["edits"].items() if e},
        )
        try:
            pdf, report = export_pdf(*args, {**options, "password": password}, s["pdf_bytes"])
        except Exception as exc:  # noqa: BLE001 - shown to the user
            st.error(f"The PDF could not be prepared. {exc}")
        else:
            label = "Download searchable PDF" if searchable else "Download PDF"
            st.download_button(label, pdf, file_name=f"{name}-{'searchable' if searchable else 'edited'}.pdf",
                               mime="application/pdf", type="primary", use_container_width=True)
            size = f"{len(pdf) / 1024:.0f} KB."
            st.markdown(
                f'<p class="fd-cap">{size} The text sits invisibly on top of the page images.</p>'
                if searchable else
                f'<p class="fd-cap">{size} Read the pages to make this PDF searchable.</p>',
                unsafe_allow_html=True)
            loose = [p for p, mode in report["layers"].items() if mode == "page"]
            if loose:
                st.markdown(
                    f'<div class="fd-status">On page {places(sorted(loose, key=position))} your corrections changed '
                    "the number of lines, so the text is searchable but will not line up exactly with the scan.</div>",
                    unsafe_allow_html=True)
        text = P.build_text(order, s["texts"], s["removed"])
        st.download_button("Download text (.txt)", text.encode("utf-8"), file_name=f"{name}.txt", mime="text/plain",
                           use_container_width=True, disabled=not text)

        st.markdown('<h2 class="fd-h2" style="margin-top:20px">Other formats</h2>'
                    '<p class="fd-help">Each page as its own file, zipped. These are never password-protected.</p>',
                    unsafe_allow_html=True)
        stamp = hashlib.sha256(repr((args, options)).encode()).hexdigest()
        if s.get("bundle_for") != stamp:
            s.pop("bundles", None)
        z1, z2 = st.columns(2)
        make_images = z1.button("Prepare page images", use_container_width=True)
        make_pdfs = z2.button("Prepare one PDF per page", use_container_width=True)
        if make_images or make_pdfs:
            try:
                plain, _ = export_pdf(*args, {**options, "password": ""}, s["pdf_bytes"])
                bundles = s.setdefault("bundles", {})
                if make_images:
                    bundles["images"] = P.pages_as_images(plain)
                else:
                    bundles["pdfs"] = P.pages_as_pdfs(plain)
                s["bundle_for"] = stamp
            except Exception as exc:  # noqa: BLE001
                st.error(f"The files could not be prepared. {exc}")
        bundles = s.get("bundles", {})
        if "images" in bundles:
            z1.download_button("Download images (.zip)", bundles["images"], file_name=f"{name}-images.zip",
                               mime="application/zip", use_container_width=True)
        if "pdfs" in bundles:
            z2.download_button("Download PDFs (.zip)", bundles["pdfs"], file_name=f"{name}-pages.zip",
                               mime="application/zip", use_container_width=True)
    st.markdown(
        '<p class="fd-help" style="margin-top:22px">Recognition is never perfect. Check names, amounts and dates '
        "against the page before relying on the text.</p>", unsafe_allow_html=True)


# --------------------------------------------------------------------------

if "pdf_bytes" not in st.session_state:
    landing()
else:
    top_bar()
    tab_read, tab_edit, tab_pages, tab_export = st.tabs(["Read and correct", "Edit", "Arrange pages", "Export"])
    with tab_read:
        read_tab()
    with tab_edit:
        edit_tab()
    with tab_pages:
        pages_tab()
    with tab_export:
        export_tab()
