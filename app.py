"""FolioDesk: edit PDFs in the browser, and make scanned ones searchable.

This file is the Streamlit interface only. All document work lives in processing.py.
"""
from __future__ import annotations

import hashlib
import html
import uuid
from pathlib import Path

import streamlit as st
from PIL import ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates

import processing as P

SAMPLE = Path(__file__).parent / "samples" / "sample-scan.pdf"
PREVIEW_DPI = 110
EDIT_DPI = 120
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
[data-testid="stMainBlockContainer"] { max-width:1480px; padding-top:1.6rem; padding-bottom:3rem; }
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
.fd-h2, [data-testid="stMarkdownContainer"] h2.fd-h2 { font-weight:700; font-size:21px; line-height:1.3;
          padding:0; margin:4px 0 2px; }
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

/* The editor's tool palette: a radio group dressed as a column of tools. */
.st-key-tool_pick [role="radiogroup"] { gap:2px; background:var(--sheet); border:1px solid var(--line);
  border-radius:8px; padding:6px; }
.st-key-tool_pick [role="radiogroup"] label { width:100%; margin:0; padding:7px 9px; border-radius:5px; cursor:pointer; }
.st-key-tool_pick [role="radiogroup"] label > div:first-child { display:none; }
.st-key-tool_pick [role="radiogroup"] label p { font-size:15.5px; font-weight:600; white-space:nowrap; }
.st-key-tool_pick [role="radiogroup"] label:hover { background:var(--desk); }
.st-key-tool_pick [role="radiogroup"] label:has(input:checked) { background:var(--ink); }
.st-key-tool_pick [role="radiogroup"] label:has(input:checked) * { color:#fff; }
.st-key-tool_pick [role="radiogroup"] label:has(input:focus-visible) { outline:3px solid var(--stamp); outline-offset:1px; }
[class*="st-key-pick_"] button { justify-content:flex-start; text-align:left; font-weight:500; min-height:38px; }
[class*="st-key-pick_"] button p { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
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


@st.cache_data(show_spinner=False, max_entries=40)
def page_image(file_key: str, page_no: int, dpi: int, rotation: int, _pdf_bytes: bytes,
               edits: list | None = None, fields: dict | None = None):
    return P.render_page(_pdf_bytes, page_no, dpi, rotation, edits, fields)


@st.cache_data(show_spinner=False, max_entries=64)
def turn_map(file_key: str, page_no: int, from_rotation: int, to_rotation: int, _pdf_bytes: bytes):
    return P.rotation_map(_pdf_bytes, page_no, from_rotation, to_rotation)


@st.cache_data(show_spinner=False, max_entries=64)
def find(file_key: str, page_no: int, query: str, rotation: int, read_stamp, _pdf_bytes: bytes, _result):
    return P.find_text(_pdf_bytes, page_no, query, rotation, _result)


@st.cache_data(show_spinner=False, max_entries=8)
def form_fields(file_key: str, _pdf_bytes: bytes) -> list[dict]:
    return P.form_fields(_pdf_bytes)


@st.cache_data(show_spinner=False, max_entries=64)
def page_size(file_key: str, page_no: int, rotation: int, _pdf_bytes: bytes) -> tuple[float, float]:
    image = P.render_page(_pdf_bytes, page_no, 72, rotation)
    return float(image.width), float(image.height)


@st.cache_data(show_spinner=False, max_entries=8)
def export_pdf(file_key: str, rotations: dict, removed: tuple, results: dict, texts: dict,
               text_layer: bool, order: list, edits: dict, fields: dict, options: dict, _pdf_bytes: bytes):
    return P.build_pdf(_pdf_bytes, rotations, removed, None, results, texts, text_layer,
                       order=order, edits=edits, fields=fields, **options)


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
        order=list(range(pages)), current=0, edit_page=0,
        results={},    # page -> recognition result
        texts={},      # page -> text as corrected by the user
        rotations={},  # page -> extra clockwise degrees
        edits={},      # page -> list of objects drawn on it, bottom first
        fields={},     # form field name -> value typed in
        removed=set(), notices=[],
        selected=None,           # id of the object being worked on
        defaults={kind: dict(look) for kind, look in DEFAULTS.items()},
        history=[], future=[],   # snapshots for undo and redo
        rev=0, finds=0, seen={},  # counters that reset widgets; canvas events already handled
    )


# What undo and redo restore. Recognition results are kept out: they are
# re-usable facts about the pages, not something the user did.
def snapshot() -> dict:
    s = st.session_state
    return {
        "pdf_bytes": s["pdf_bytes"], "file_key": s["file_key"], "order": list(s["order"]),
        "rotations": dict(s["rotations"]), "removed": set(s["removed"]), "fields": dict(s["fields"]),
        "edits": {p: [dict(e) for e in items] for p, items in s["edits"].items()},
    }


def checkpoint() -> None:
    """Remember the document as it is now. Call before every change the user makes."""
    s = st.session_state
    s["history"].append(snapshot())
    del s["history"][:-60]
    s["future"].clear()


def restore(state: dict) -> None:
    s = st.session_state
    s.update(state)
    s["rev"] += 1
    if not any(e.get("id") == s["selected"] for items in s["edits"].values() for e in items):
        s["selected"] = None
    # Pages added after this point are gone again; so is what was read from them.
    for page_no in [p for p in s["results"] if p not in s["order"]]:
        forget_reading(page_no)


def forget_reading(page_no: int) -> None:
    s = st.session_state
    s["results"].pop(page_no, None)
    s["texts"].pop(page_no, None)
    s.pop(f"text_{page_no}", None)


def undo() -> None:
    s = st.session_state
    if s["history"]:
        s["future"].append(snapshot())
        restore(s["history"].pop())


def redo() -> None:
    s = st.session_state
    if s["future"]:
        s["history"].append(snapshot())
        restore(s["future"].pop())


def change(action, *args) -> None:
    """Run a change to the document so that it can be undone."""
    checkpoint()
    action(*args)


def filled_fields() -> dict | None:
    return dict(st.session_state["fields"]) or None


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
    checkpoint()
    first_new = max(s["order"]) + 1
    s["pdf_bytes"] = merged
    s["file_key"] = hashlib.sha256(merged).hexdigest()
    s["order"].extend(range(first_new, first_new + added))
    s["notices"].append(f"Added {plural(added, 'page')} at the end")


def grow(pdf_bytes: bytes) -> int:
    """Swap in a PDF that has one more page at the end. Returns the new page's index."""
    s = st.session_state
    new = max(s["order"]) + 1
    s["pdf_bytes"] = pdf_bytes
    s["file_key"] = hashlib.sha256(pdf_bytes).hexdigest()
    forget_reading(new)
    return new


def duplicate(page_no: int) -> None:
    s = st.session_state
    try:
        new = grow(P.duplicate_page(s["pdf_bytes"], page_no))
    except P.PdfError as exc:
        s["notices"].append(str(exc))
        return
    s["order"].insert(s["order"].index(page_no) + 1, new)
    if page_no in s["rotations"]:
        s["rotations"][new] = s["rotations"][page_no]
    if s["edits"].get(page_no):
        s["edits"][new] = [{**e, "id": uuid.uuid4().hex[:10]} for e in s["edits"][page_no]]
    if page_no in s["results"]:
        s["results"][new] = {**s["results"][page_no], "page": new}
        set_text(new, s["texts"].get(page_no, s["results"][page_no]["text"]))


def add_blank() -> None:
    s = st.session_state
    try:
        s["order"].append(grow(P.add_blank_page(s["pdf_bytes"], s["order"][-1])))
    except P.PdfError as exc:
        s["notices"].append(str(exc))
        return
    s["notices"].append("Added a blank page at the end")


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
            '<h1 class="fd-h1">Edit a PDF, even a scanned one.</h1>'
            '<p class="fd-lede">Change the wording, add text, shapes, stamps and signatures, black out private '
            "details, fill in forms and rearrange pages. Scans in English or Hindi can be read first, so their "
            "text becomes searchable and editable too.</p>",
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
            st.warning(f"Text recognition is unavailable: {status['detail']} Everything else still works.")
    with right:
        if SAMPLE.exists():
            sample = SAMPLE.read_bytes()
            st.image(page_image("sample", 0, 80, 0, sample), use_container_width=True)
            st.markdown(
                '<p class="fd-cap">No PDF to hand? The sample is a two-page housing society notice in English '
                "and Hindi, scanned, with one page sideways.</p>",
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
    bar, back, forward, action = st.columns([5, 0.55, 0.55, 1], vertical_alignment="bottom")
    with bar:
        st.markdown(
            f'<div class="fd-top" style="margin-bottom:0;border-bottom:0;padding-bottom:0">'
            f'<span class="fd-brand">FolioDesk</span>'
            f'<span class="fd-file">{html.escape(s["filename"])}</span>'
            f'<span class="fd-meta">{meta}</span></div>',
            unsafe_allow_html=True,
        )
    back.button(":material/undo:", help="Undo the last change", on_click=undo, disabled=not s["history"],
                use_container_width=True)
    forward.button(":material/redo:", help="Redo", on_click=redo, disabled=not s["future"],
                   use_container_width=True)
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
    if any(r["rotation"] != before.get(p, 0) for p, r in results.items()):
        checkpoint()  # turning a page upright is a change to the document, so it can be undone
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
        image = page_image(s["file_key"], page_no, PREVIEW_DPI, rotation, s["pdf_bytes"], s["edits"].get(page_no),
                           filled_fields())
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
    per_row = 4
    for start in range(0, count, per_row):
        cols = st.columns(per_row, gap="medium")
        for col, page_no in zip(cols, list(order[start:start + per_row])):
            with col:
                place = position(page_no)
                rotation = s["rotations"].get(page_no, 0)
                gone = page_no in s["removed"]
                thumb = page_image(s["file_key"], page_no, THUMB_DPI, rotation, s["pdf_bytes"],
                                   s["edits"].get(page_no), filled_fields())
                st.image(P.dim_image(thumb) if gone else thumb, use_container_width=True)
                if gone:
                    st.markdown(f'<p class="fd-cap removed">Page {place}, removed</p>', unsafe_allow_html=True)
                else:
                    turned = f", turned {rotation}°" if rotation else ""
                    st.markdown(f'<p class="fd-cap">Page {place}{turned}</p>', unsafe_allow_html=True)
                m1, m2, b1, b2, b3 = st.columns(5, gap="small")
                m1.button(":material/arrow_back:", key=f"e{page_no}", help="Move earlier", disabled=place == 1,
                          on_click=change, args=(move, page_no, -1), use_container_width=True)
                m2.button(":material/arrow_forward:", key=f"f{page_no}", help="Move later", disabled=place == count,
                          on_click=change, args=(move, page_no, 1), use_container_width=True)
                b1.button(":material/rotate_left:", key=f"l{page_no}", help="Turn left", disabled=gone,
                          on_click=change, args=(rotate, page_no, 270), use_container_width=True)
                b2.button(":material/rotate_right:", key=f"r{page_no}", help="Turn right", disabled=gone,
                          on_click=change, args=(rotate, page_no, 90), use_container_width=True)
                b3.button(":material/content_copy:", key=f"d{page_no}", help="Duplicate this page",
                          disabled=gone or count >= P.MAX_PAGES, on_click=change, args=(duplicate, page_no),
                          use_container_width=True)
                st.button("Restore" if gone else "Remove", key=f"x{page_no}", on_click=change,
                          args=(toggle_removed, page_no), use_container_width=True, disabled=not gone and kept <= 1,
                          help="The last remaining page cannot be removed." if not gone and kept <= 1 else None)

    room = P.MAX_PAGES - count
    st.markdown('<h2 class="fd-h2" style="margin-top:26px">Add pages</h2>', unsafe_allow_html=True)
    if room <= 0:
        st.markdown(f'<p class="fd-help">This document is at the {P.MAX_PAGES}-page limit.</p>', unsafe_allow_html=True)
        return
    c_file, c_blank = st.columns([3, 1], vertical_alignment="bottom")
    extra = c_file.file_uploader(
        f"From another PDF. The pages go at the end; move them from there. Room for {plural(room, 'more page')}.",
        type=["pdf"], key=f"merge_{s['uploads']}_{len(s['pdf_bytes'])}")
    if extra is not None:
        add_pages(extra.getvalue())
        s["uploads"] += 1
        st.rerun()
    c_blank.button("Add a blank page", on_click=change, args=(add_blank,), use_container_width=True)


# --------------------------------------------------------------------------
# Editor
# --------------------------------------------------------------------------

# key, name, icon, what to do with it
TOOLS = [
    ("select", "Select", "arrow_selector_tool", "Click something you added to select it. Drag it to move it."),
    ("edittext", "Edit text", "edit_note", "Click a line of text on the page to change its wording."),
    ("text", "Add text", "title", "Drag a box where the text should go, or click for a standard one. Then type."),
    ("highlight", "Highlight", "format_ink_highlighter", "Drag across the part to mark, or click a line of text."),
    ("underline", "Underline", "format_underlined", "Drag across the part to underline, or click a line of text."),
    ("strike", "Strike out", "format_strikethrough", "Drag across the part to strike out, or click a line of text."),
    ("rect", "Rectangle", "rectangle", "Drag from one corner to the opposite one."),
    ("ellipse", "Ellipse", "circle", "Drag from one corner to the opposite one."),
    ("line", "Line", "horizontal_rule", "Drag from where the line starts to where it ends."),
    ("arrow", "Arrow", "arrow_right_alt", "Drag from the tail to where the arrow should point."),
    ("image", "Image", "image", "Choose a picture in the panel, then drag where it should go. Good for signatures."),
    ("stamp", "Stamp", "approval", "Drag a box for the stamp, or click for a standard one."),
    ("comment", "Comment", "sticky_note_2", "Click where the note belongs, then type it. Readers see an icon they can open."),
    ("cover", "Cover up", "ink_eraser", "Drag over the part to hide. What is underneath stays in the file."),
    ("redact", "Redact", "visibility_off", "Drag over the part to black out. What is underneath is deleted for good."),
]
TOOL = {key: {"name": name, "icon": icon, "hint": hint} for key, name, icon, hint in TOOLS}
KIND_NAME = {**{key: TOOL[key]["name"] for key in TOOL if key in P.EDIT_KINDS},
             "text": "Text", "replace": "Edited text", "cover": "Covered area", "redact": "Redaction"}
FONT_NAME = {"sans": "Sans serif", "serif": "Serif", "mono": "Typewriter"}
ALIGN_NAME = {"left": "Left", "center": "Centre", "right": "Right"}
STAMPS = ["APPROVED", "PAID", "DRAFT", "CONFIDENTIAL", "COPY", "RECEIVED", "VOID"]
OWN_WORDS = "Your own words"
FIND_VERB = {"highlight": "Highlight", "underline": "Underline", "strike": "Strike out", "redact": "Redact"}

# How a new object of each kind starts out. The panel changes these per session.
DEFAULTS = {
    "text": {"text": "", "size": 12.0, "color": "#16202c", "font": "sans", "bold": False, "italic": False,
             "align": "left"},
    "highlight": {"color": "#ffd60a"},
    "underline": {"color": "#1f3fbf"},
    "strike": {"color": "#b93520"},
    "cover": {"color": "#ffffff"},
    "rect": {"color": "#b93520", "width": 2.0, "fill": "", "opacity": 100},
    "ellipse": {"color": "#b93520", "width": 2.0, "fill": "", "opacity": 100},
    "line": {"color": "#b93520", "width": 2.0},
    "arrow": {"color": "#b93520", "width": 2.0},
    "stamp": {"text": "APPROVED", "color": "#1f3fbf"},
    "comment": {"text": ""},
}


def describe(edit: dict) -> str:
    kind = edit["kind"]
    words = " ".join(edit.get("text", "").split())
    if kind == "replace":
        if edit.get("changed") is False:
            return f"Editing: {words[:34]}"
        return f"Text now: {words[:34]}" if words else "Text removed"
    if kind in ("text", "comment", "stamp"):
        return f"{KIND_NAME[kind]}: {words[:36]}" if words else f"{KIND_NAME[kind]} (empty)"
    return KIND_NAME[kind]


def is_idle(edit: dict) -> bool:
    """Objects that would change nothing: an empty text box, a line picked but not reworded."""
    if edit["kind"] == "replace":
        return edit.get("changed") is False
    return edit["kind"] in ("text", "comment", "stamp") and not edit.get("text", "").strip()


def select(edit_id: str | None = None) -> None:
    """Select one object (or none) and sweep away idle ones left behind."""
    s = st.session_state
    for page, items in s["edits"].items():
        s["edits"][page] = [e for e in items if e.get("id") == edit_id or not is_idle(e)]
    s["selected"] = edit_id


def add_object(page_no: int, kind: str, rect: list[float], record: bool = True, **extra) -> None:
    s = st.session_state
    if record:
        checkpoint()
    edit = {"id": uuid.uuid4().hex[:10], "kind": kind, "rect": [round(v, 2) for v in rect],
            "rotation": s["rotations"].get(page_no, 0), **s["defaults"].get(kind, {}), **extra}
    s["edits"].setdefault(page_no, []).append(edit)
    select(edit["id"])


def shown_edits(page_no: int, rotation: int) -> list[dict]:
    """The page's objects with their geometry as it sits at the page's current rotation."""
    s = st.session_state
    return [
        e if e.get("rotation", 0) == rotation
        else P.transformed(e, turn_map(s["file_key"], page_no, e.get("rotation", 0), rotation, s["pdf_bytes"]))
        for e in s["edits"].get(page_no, [])
    ]


def canvas_event(key: str, width: float, height: float):
    """The newest click or drag on the page, in PDF points, or None if it was already handled."""
    s = st.session_state
    raw = s.get(key)
    if not raw or not raw.get("width"):
        return None
    stamp = raw.get("unix_time") or tuple(raw.values())
    if s["seen"].get(key) == stamp:
        return None
    s["seen"][key] = stamp
    k = width / raw["width"]
    a = (min(width, max(0.0, raw["x1"] * k)), min(height, max(0.0, raw["y1"] * k)))
    b = (min(width, max(0.0, raw["x2"] * k)), min(height, max(0.0, raw["y2"] * k)))
    return a, b, abs(b[0] - a[0]) >= 4 or abs(b[1] - a[1]) >= 4


def on_canvas(page_no: int, rotation: int, tool: str, a: tuple, b: tuple, dragged: bool,
              size: tuple[float, float], result: dict | None) -> None:
    """Act on a click (a == b, near enough) or a drag from a to b with the current tool."""
    s = st.session_state
    edits = s["edits"].setdefault(page_no, [])
    hit = P.hit_test(shown_edits(page_no, rotation), *a)
    width, height = size
    box = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
    roomy = box[2] - box[0] >= 3 and box[3] - box[1] >= 3

    if tool == "select":
        if hit is None:
            select(None)
            return
        target = edits[hit]
        if dragged:
            checkpoint()
            start, stop = a, b
            drawn_at = target.get("rotation", 0)
            if drawn_at != rotation:  # placed before the page was turned: move it in its own space
                m = turn_map(s["file_key"], page_no, rotation, drawn_at, s["pdf_bytes"])
                start, stop = P.carry(a, m), P.carry(b, m)
            s["edits"][page_no][hit] = P.moved(target, stop[0] - start[0], stop[1] - start[1])
            s["rev"] += 1
        select(target["id"])
        return

    # In any tool, a plain click on something already there selects it.
    if not dragged and hit is not None and (tool != "edittext" or edits[hit]["kind"] in ("replace", "text")):
        select(edits[hit]["id"])
        return

    if tool == "edittext":
        line = P.text_line_at(s["pdf_bytes"], page_no, a[0], a[1], rotation, result)
        if line is not None:
            add_object(page_no, "replace", line.pop("rect"), record=False, changed=False, **line)
        elif hit is not None:
            select(edits[hit]["id"])
        elif result is None and engine()["ok"]:
            st.toast("No text found there. If this page is a scan, read it first under Read text.")
        else:
            st.toast("No text found there. Click right on a line of text.")
    elif tool in ("highlight", "underline", "strike") and not dragged:
        line = P.text_line_at(s["pdf_bytes"], page_no, a[0], a[1], rotation, result)
        if line is not None:
            add_object(page_no, tool, line["rect"])
        else:
            st.toast("Drag across the part to mark.")
    elif tool == "text":
        default = s["defaults"]["text"]["size"] * 1.5 + 8
        add_object(page_no, "text", box if dragged and roomy
                   else [a[0], a[1], min(width, a[0] + 240), min(height, a[1] + default)])
    elif tool == "comment":
        add_object(page_no, "comment", [a[0], a[1], a[0] + 18, a[1] + 18])
    elif tool == "stamp":
        add_object(page_no, "stamp", box if dragged and roomy else [a[0] - 75, a[1] - 22, a[0] + 75, a[1] + 22])
    elif tool == "image":
        data = s.get("tool_image")
        if not data:
            st.toast("Choose a picture in the panel first.")
            return
        iw, ih = P.image_size(data)
        if dragged and roomy:
            k = min((box[2] - box[0]) / iw, (box[3] - box[1]) / ih)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            rect = [cx - iw * k / 2, cy - ih * k / 2, cx + iw * k / 2, cy + ih * k / 2]
        else:
            w = min(180.0, width / 3)
            rect = [a[0], a[1], a[0] + w, a[1] + w * ih / iw]
        add_object(page_no, "image", rect, data=data)
    elif tool in ("line", "arrow"):
        if dragged:
            add_object(page_no, tool, box, line=[round(v, 2) for v in (*a, *b)])
        else:
            st.toast(TOOL[tool]["hint"])
    elif dragged and roomy:  # rectangle, ellipse, cover, redact, and dragged markup
        add_object(page_no, tool, box)
    else:
        st.toast(TOOL[tool]["hint"])


def controls(target: dict, kind: str, key: str, live: bool) -> bool:
    """The look of one object (live) or of the next one a tool will make. Returns True on change."""
    new: dict = {}
    if live and kind in ("text", "replace", "comment"):
        label = {"text": "Text", "replace": "Wording", "comment": "Note"}[kind]
        new["text"] = st.text_area(label, target.get("text", ""), key=f"{key}_text",
                                   height=90 if kind == "replace" else 120,
                                   placeholder="Type here, then press Ctrl+Enter or click away")
    if kind == "stamp":
        words = target.get("text", "")
        preset = st.selectbox("Wording", STAMPS + [OWN_WORDS], key=f"{key}_preset",
                              index=STAMPS.index(words) if words in STAMPS else len(STAMPS))
        new["text"] = preset if preset != OWN_WORDS else st.text_input(
            "Your wording", "" if words in STAMPS else words, max_chars=30, key=f"{key}_own").strip()
        new["color"] = st.color_picker("Colour", target.get("color", "#1f3fbf"), key=f"{key}_color")
    elif kind in ("text", "replace"):
        c_font, c_size = st.columns(2)
        new["font"] = c_font.selectbox("Font", P.FONT_FAMILIES, format_func=FONT_NAME.get, key=f"{key}_font",
                                       index=P.FONT_FAMILIES.index(target.get("font", "sans")))
        new["size"] = c_size.number_input("Size", 4.0, 144.0, float(target.get("size") or 12), 1.0,
                                          key=f"{key}_size")
        c_bold, c_italic, c_color = st.columns(3, vertical_alignment="bottom")
        new["bold"] = c_bold.checkbox("Bold", bool(target.get("bold")), key=f"{key}_bold")
        new["italic"] = c_italic.checkbox("Italic", bool(target.get("italic")), key=f"{key}_italic")
        new["color"] = c_color.color_picker("Colour", target.get("color", "#16202c"), key=f"{key}_color")
        if kind == "text":
            sides = list(ALIGN_NAME)
            new["align"] = st.radio("Line up", sides, format_func=ALIGN_NAME.get, horizontal=True,
                                    index=sides.index(target.get("align", "left")), key=f"{key}_align")
        elif target.get("erase") == "pixels":
            new["fill"] = st.color_picker("Paper colour", target.get("fill", "#ffffff"), key=f"{key}_fill",
                                          help="Painted over the old wording. Picked from the scan; adjust it "
                                               "if the patch shows.")
    elif kind in ("highlight", "underline", "strike", "cover"):
        new["color"] = st.color_picker("Colour", target.get("color", "#ffd60a"), key=f"{key}_color")
    elif kind in ("rect", "ellipse"):
        c_color, c_width = st.columns(2)
        new["color"] = c_color.color_picker("Outline", target.get("color", "#b93520"), key=f"{key}_color")
        new["width"] = c_width.number_input("Thickness", 0.0, 24.0, float(target.get("width", 2)), 0.5,
                                            key=f"{key}_width", help="0 for no outline.")
        filled = st.checkbox("Fill it in", bool(target.get("fill")), key=f"{key}_filled")
        new["fill"] = st.color_picker("Fill", target.get("fill") or "#ffd60a", key=f"{key}_fill") if filled else ""
        new["opacity"] = st.slider("Opacity", 10, 100, int(target.get("opacity", 100)), 5, format="%d%%",
                                   key=f"{key}_opacity")
    elif kind in ("line", "arrow"):
        c_color, c_width = st.columns(2)
        new["color"] = c_color.color_picker("Colour", target.get("color", "#b93520"), key=f"{key}_color")
        new["width"] = c_width.number_input("Thickness", 0.5, 24.0, float(target.get("width", 2)), 0.5,
                                            key=f"{key}_width")
    changes = {k: v for k, v in new.items() if target.get(k) != v}
    if not changes:
        return False
    if live:
        checkpoint()
        if kind == "replace":
            changes["changed"] = True
    target.update(changes)
    return True


def object_panel(page_no: int, rotation: int, current: dict) -> None:
    """Everything about the selected object."""
    s = st.session_state
    edits = s["edits"][page_no]
    kind = current["kind"]
    key = f"o_{current['id']}_{s['rev']}"
    st.markdown(f'<h2 class="fd-h2">{KIND_NAME[kind]}</h2>', unsafe_allow_html=True)
    if kind == "replace":
        st.markdown(f'<p class="fd-cap">Was: {html.escape(current.get("original", ""))}</p>', unsafe_allow_html=True)
    controls(current, kind, key, live=True)
    if kind == "redact":
        st.markdown('<p class="fd-help">Everything under this block is deleted from the downloaded file, '
                    "including from a scan's image.</p>", unsafe_allow_html=True)
    elif kind == "image":
        st.markdown('<p class="fd-help">Drag it with Select to move it. Resize it below.</p>', unsafe_allow_html=True)

    index = edits.index(current)
    if kind not in ("line", "arrow", "comment") and current.get("rotation", 0) == rotation:
        with st.expander("Position and size"):
            x0, y0, x1, y1 = current["rect"]
            c_x, c_y = st.columns(2)
            c_w, c_h = st.columns(2)
            left = c_x.number_input("From left", value=round(float(x0), 1), step=2.0, key=f"{key}_x")
            top = c_y.number_input("From top", value=round(float(y0), 1), step=2.0, key=f"{key}_y")
            wide = c_w.number_input("Width", 2.0, value=max(2.0, round(float(x1 - x0), 1)), step=2.0, key=f"{key}_w")
            tall = c_h.number_input("Height", 2.0, value=max(2.0, round(float(y1 - y0), 1)), step=2.0, key=f"{key}_h")
            st.markdown('<p class="fd-cap">In points: 72 to the inch, about 28 to the centimetre.</p>',
                        unsafe_allow_html=True)
            wanted = [left, top, left + wide, top + tall]
            if any(abs(p - q) > 0.06 for p, q in zip(wanted, [round(v, 1) for v in (x0, y0, x1, y1)])):
                checkpoint()
                edits[index] = P.resized(current, wanted)
                st.rerun()

    b_copy, b_front, b_back = st.columns(3)
    if b_copy.button("Duplicate", key=f"{key}_copy", use_container_width=True):
        checkpoint()
        twin = {**P.moved(current, 12, 12), "id": uuid.uuid4().hex[:10]}
        edits.append(twin)
        select(twin["id"])
        st.rerun()
    if b_front.button("To front", key=f"{key}_front", use_container_width=True, disabled=index == len(edits) - 1):
        checkpoint()
        edits.append(edits.pop(index))
        st.rerun()
    if b_back.button("To back", key=f"{key}_back", use_container_width=True, disabled=index == 0):
        checkpoint()
        edits.insert(0, edits.pop(index))
        st.rerun()
    b_done, b_delete = st.columns(2)
    if b_done.button("Done", key=f"{key}_done", type="primary", use_container_width=True):
        select(None)
        st.rerun()
    if b_delete.button("Delete", key=f"{key}_delete", use_container_width=True):
        checkpoint()
        edits.pop(index)
        select(None)
        st.rerun()


def tool_panel(page_no: int, rotation: int, tool: str, result: dict | None) -> None:
    """What the current tool will do, shown while nothing is selected."""
    s = st.session_state
    st.markdown(f'<h2 class="fd-h2">{TOOL[tool]["name"]}</h2>'
                f'<p class="fd-help">{TOOL[tool]["hint"]}</p>', unsafe_allow_html=True)
    read_stamp = result and (result["rotation"], result["dpi"], result["lang"], len(result["words"]))

    if tool == "image":
        picture = st.file_uploader("Picture (PNG or JPEG)", type=["png", "jpg", "jpeg"], key="tool_image_file")
        clear = st.checkbox("Remove the white background", key="tool_image_clear",
                            help="For a signature photographed or scanned on white paper.")
        if picture is None:
            s["tool_image"] = None
            s.pop("tool_image_for", None)
        elif s.get("tool_image_for") != (picture.file_id, clear):
            raw = picture.getvalue()
            try:
                P.image_size(raw)
                s["tool_image"] = P.transparent_background(raw) if clear else raw
            except P.PdfError as exc:
                s["tool_image"] = None
                st.error(str(exc))
            s["tool_image_for"] = (picture.file_id, clear)
        if s.get("tool_image"):
            st.image(s["tool_image"], width=180)
    elif tool in s["defaults"]:
        controls(s["defaults"][tool], tool, f"d_{tool}", live=False)

    if tool in FIND_VERB:
        query = st.text_input("Or find words on this page", key=f"find_{tool}_{s['finds']}",
                              placeholder="Sahyog Housing Society")
        if query.strip():
            matches = find(s["file_key"], page_no, query, rotation, read_stamp, s["pdf_bytes"], result)
            if st.button(f"{FIND_VERB[tool]} {plural(len(matches), 'match') if len(matches) == 1 else f'{len(matches)} matches'}",
                         type="primary", use_container_width=True, disabled=not matches):
                checkpoint()
                for m in matches:
                    add_object(page_no, tool, m["rect"], record=False)
                select(None)
                s["finds"] += 1
                st.rerun()
            if not matches:
                st.markdown('<p class="fd-cap">Those words were not found on this page. Check the spelling, '
                            "or read the page first if it is a scan.</p>", unsafe_allow_html=True)
    elif tool == "edittext":
        st.markdown('<h2 class="fd-h2" style="margin-top:14px">Find and replace</h2>', unsafe_allow_html=True)
        query = st.text_input("Find", key=f"swap_find_{s['finds']}", placeholder="12 November 2026")
        swap = st.text_input("Replace with", key=f"swap_with_{s['finds']}", placeholder="14 November 2026",
                             help="Leave empty to remove the words.")
        pages = [p for p in s["order"] if p not in s["removed"]]
        everywhere = len(pages) > 1 and st.checkbox("On every page", key="swap_all")
        if query.strip():
            found = {}
            for p in (pages if everywhere else [page_no]):
                turned = s["rotations"].get(p, 0)
                read = s["results"].get(p)
                if read and is_stale(p):
                    read = None
                stamp = read and (read["rotation"], read["dpi"], read["lang"], len(read["words"]))
                found[p] = find(s["file_key"], p, query, turned, stamp, s["pdf_bytes"], read)
            total = sum(len(m) for m in found.values())
            if st.button(f"Replace {plural(total, 'match') if total == 1 else f'{total} matches'}", type="primary",
                         use_container_width=True, disabled=not total):
                checkpoint()
                for p, matches in found.items():
                    for m in matches:
                        add_object(p, "replace", m["rect"], record=False, text=swap.strip(), changed=True,
                                   **{k: v for k, v in m.items() if k != "rect"})
                select(None)
                s["finds"] += 1
                st.rerun()
            if not total:
                st.markdown('<p class="fd-cap">Those words were not found. Check the spelling, or read the pages '
                            "first if they are scans.</p>", unsafe_allow_html=True)
    elif tool == "select" and not s["edits"].get(page_no):
        st.markdown('<p class="fd-help">Nothing has been added to this page yet. Pick a tool on the left to '
                    "start: <b>Edit text</b> changes wording already on the page, the others add to it.</p>",
                    unsafe_allow_html=True)


def form_panel(page_no: int) -> None:
    s = st.session_state
    fields = [f for f in form_fields(s["file_key"], s["pdf_bytes"]) if f["page"] == page_no]
    if not fields:
        return
    with st.expander(f"Fill in the form ({plural(len(fields), 'field')} on this page)", expanded=True):
        for i, field in enumerate(fields):
            name = field["name"]
            value = s["fields"].get(name, field["value"])
            key = f"ff_{page_no}_{i}_{s['rev']}"
            if field["kind"] == "checkbox":
                new = st.checkbox(field["label"], bool(value), key=key)
            elif field["kind"] == "choice" and field["options"]:
                options = [""] + [o for o in field["options"] if o]
                new = st.selectbox(field["label"], options, key=key,
                                   index=options.index(value) if value in options else 0)
            elif field["multiline"]:
                new = st.text_area(field["label"], str(value), key=key, height=90)
            else:
                new = st.text_input(field["label"], str(value), key=key)
            if new != value:
                checkpoint()
                s["fields"][name] = new


def layers_panel(page_no: int) -> None:
    """Everything added to the page, topmost first."""
    s = st.session_state
    edits = s["edits"].get(page_no, [])
    if not edits:
        return
    st.markdown(f'<h2 class="fd-h2" style="margin-top:22px">On page {position(page_no)}</h2>',
                unsafe_allow_html=True)
    for edit in reversed(list(edits)):
        chosen = edit.get("id") == s["selected"]
        line, drop = st.columns([5, 1], vertical_alignment="center")
        if line.button(describe(edit), key=f"pick_{edit['id']}", use_container_width=True,
                       type="primary" if chosen else "secondary", icon=f":material/{ICON.get(edit['kind'], 'edit')}:"):
            select(None if chosen else edit["id"])
            st.rerun()
        if drop.button(":material/delete:", key=f"drop_{edit['id']}", help="Delete", use_container_width=True):
            checkpoint()
            s["edits"][page_no] = [e for e in edits if e is not edit]
            if chosen:
                s["selected"] = None
            st.rerun()
    if any(e["kind"] == "redact" for e in edits) and s["texts"].get(page_no, "").strip():
        st.markdown(
            '<div class="fd-status warn" style="margin-top:10px">This page\'s recognised text may still contain '
            "the redacted words. Delete them under Read text before downloading the text file. "
            "The searchable layer is left off redacted pages.</div>", unsafe_allow_html=True)


ICON = {**{key: TOOL[key]["icon"] for key in TOOL}, "replace": "edit_note"}


def outline(image, rect: list[float], scale: float):
    """Mark the selected object on the page preview."""
    image = image.copy()
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = (v * scale for v in rect)
    x0, y0, x1, y1 = x0 - 3, y0 - 3, x1 + 3, y1 + 3
    blue = (31, 63, 191)
    draw.rectangle([x0, y0, x1, y1], outline=blue, width=2)
    for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        draw.rectangle([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255), outline=blue, width=2)
    return image


def edit_tab() -> None:
    s = st.session_state
    pages = [p for p in s["order"] if p not in s["removed"]]
    if s["edit_page"] not in pages:
        s["edit_page"] = pages[0]
        s["selected"] = None
    if len(pages) > 1:
        picked = st.segmented_control("Page", pages, format_func=lambda p: str(position(p)),
                                      default=s["edit_page"], key="edit_pick")
        if picked is not None and picked != s["edit_page"]:
            s["edit_page"] = picked
            select(None)
    page_no = s["edit_page"]
    rotation = s["rotations"].get(page_no, 0)
    size = page_size(s["file_key"], page_no, rotation, s["pdf_bytes"])
    result = s["results"].get(page_no)
    if result and is_stale(page_no):
        result = None
    tool = s.get("tool_pick") or "select"
    canvas_key = f"canvas_{page_no}"

    event = canvas_event(canvas_key, *size)
    if event:
        on_canvas(page_no, rotation, tool, *event, size, result)

    rail, sheet, panel = st.columns([0.62, 2.05, 1.2], gap="medium")
    with rail:
        st.radio("Tool", [key for key, *_ in TOOLS], key="tool_pick", label_visibility="collapsed",
                 format_func=lambda key: f":material/{TOOL[key]['icon']}: {TOOL[key]['name']}")
    with panel:
        edits = s["edits"].setdefault(page_no, [])
        current = next((e for e in edits if e.get("id") == s["selected"]), None)
        if current is not None:
            object_panel(page_no, rotation, current)
        else:
            tool_panel(page_no, rotation, tool, result)
        form_panel(page_no)
        layers_panel(page_no)
    with sheet:
        edits = s["edits"].get(page_no, [])
        image = page_image(s["file_key"], page_no, EDIT_DPI, rotation, s["pdf_bytes"], edits, filled_fields())
        chosen = next((e for e in shown_edits(page_no, rotation) if e.get("id") == s["selected"]), None)
        if chosen is not None:
            image = outline(image, chosen["rect"], image.width / size[0])
        streamlit_image_coordinates(image, key=canvas_key, click_and_drag=True, use_column_width="always",
                                    cursor="pointer" if tool in ("select", "edittext") else "crosshair",
                                    image_format="JPEG", jpeg_quality=88)
        if chosen is not None:  # the panel is showing the object, so keep the tool's hint in view here
            st.markdown(f'<p class="fd-cap">{TOOL[tool]["name"]}: {TOOL[tool]["hint"]}</p>', unsafe_allow_html=True)


def export_tab() -> None:
    s = st.session_state
    order = s["order"]
    kept = [p for p in order if p not in s["removed"]]
    places = lambda found: ", ".join(str(position(p)) for p in found)  # noqa: E731
    edits = {p: [e for e in items if not is_idle(e)] for p, items in s["edits"].items()}
    edits = {p: items for p, items in edits.items() if items}
    redacted = [p for p in kept if any(e["kind"] == "redact" for e in edits.get(p, []))]
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
        changed = sum(len(edits.get(p, [])) for p in kept)
        filled = len(s["fields"])
        items = [f"{plural(len(kept), 'page')}" + (f" ({len(s['removed'])} removed)" if s["removed"] else "")]
        if kept != sorted(kept):
            items.append("Pages in your new order")
        if turned:
            items.append(f"{plural(turned, 'page')} turned")
        if changed:
            items.append(f"{plural(changed, 'edit')} from the Edit tab")
        if filled:
            items.append(f"{plural(filled, 'form field')} filled in")
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
            s["file_key"], dict(s["rotations"]), tuple(sorted(s["removed"])),
            {p: s["results"][p] for p in layer_pages}, {p: s["texts"][p] for p in layer_pages if p in s["texts"]},
            bool(layer_pages), list(order), edits, dict(s["fields"]),
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
                f'<p class="fd-cap">{size} Read the pages under Read text to make this PDF searchable.</p>',
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
    tab_edit, tab_pages, tab_read, tab_export = st.tabs(["Edit", "Pages", "Read text", "Export"])
    with tab_edit:
        edit_tab()
    with tab_pages:
        pages_tab()
    with tab_read:
        read_tab()
    with tab_export:
        export_tab()
