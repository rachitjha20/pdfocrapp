"""FolioDesk: make scanned PDFs searchable, correct the text, tidy the pages.

This file is the Streamlit interface only. All document work lives in processing.py.
"""
from __future__ import annotations

import hashlib
import html
from pathlib import Path

import streamlit as st

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
def page_image(file_key: str, page_no: int, dpi: int, rotation: int, _pdf_bytes: bytes):
    return P.render_page(_pdf_bytes, page_no, dpi, rotation)


@st.cache_data(show_spinner=False, max_entries=8)
def export_pdf(file_key: str, rotations: dict, removed: tuple, notes: list, results: dict, texts: dict,
               text_layer: bool, _pdf_bytes: bytes):
    return P.build_pdf(_pdf_bytes, rotations, removed, notes, results, texts, text_layer)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

def load_document(name: str, pdf_bytes: bytes) -> None:
    """Validate a file and make it the open document. Records the reason if it can't be used."""
    try:
        pages = P.inspect_pdf(pdf_bytes)
    except P.PdfError as exc:
        st.session_state["load_error"] = str(exc)
        return
    uploads = st.session_state.get("uploads", 0)
    st.session_state.clear()
    st.session_state.update(
        uploads=uploads, file_key=hashlib.sha256(pdf_bytes).hexdigest(), filename=name, pdf_bytes=pdf_bytes,
        page_count=pages, current=0,
        results={},    # page -> recognition result
        texts={},      # page -> text as corrected by the user
        rotations={},  # page -> extra clockwise degrees
        removed=set(), notes=[], notices=[],
    )


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
        uploaded = st.file_uploader(
            f"Choose a PDF (up to {P.MAX_BYTES // 1_048_576} MB and {P.MAX_PAGES} pages)",
            type=["pdf"], key=f"upload_{st.session_state.get('uploads', 0)}",
        )
        if uploaded is not None:
            load_document(uploaded.name, uploaded.getvalue())
            if "load_error" in st.session_state:
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
    meta = f"{plural(s['page_count'], 'page')}, {len(s['pdf_bytes']) / 1024:.0f} KB"
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
        bar.progress(finished / total, text=f"Read page {page_no + 1} ({finished} of {total})")

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
            turned.append(page_no + 1)
    if turned:
        s["notices"].append("Turned upright: page " + ", ".join(map(str, sorted(turned))))
    for page_no, message in sorted(errors.items()):
        s["notices"].append(f"Page {page_no + 1} could not be read. {message}")
    if results and not errors:
        seconds = sum(r["seconds"] for r in results.values())
        s["notices"].append(f"Read {plural(len(results), 'page')} in {seconds:.1f} s of processing")


def read_tab() -> None:
    s = st.session_state
    status = engine()
    count = s["page_count"]
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

    if s["current"] >= count:
        s["current"] = 0

    def page_label(p: int) -> str:
        suffix = " (removed)" if p in s["removed"] else " ✓" if p in s["results"] else ""
        return f"{p + 1}{suffix}"

    todo = [p for p in range(count) if p not in s["removed"] and not is_edited(p)]
    with c_all:
        read_all = st.button("Read all pages", type="primary", use_container_width=True, disabled=not (can_read and todo))
    with c_one:
        read_one = st.button("Read this page", use_container_width=True, disabled=not can_read)

    if count > 1:
        picked = st.segmented_control(
            "Page", list(range(count)), format_func=page_label, default=s["current"], key="page_pick",
        )
        if picked is not None:
            s["current"] = picked
    page_no = s["current"]

    if read_all or read_one:
        kept = [p + 1 for p in range(count) if read_all and is_edited(p) and p not in s["removed"]]
        run_recognition(todo if read_all else [page_no], language, detail, straighten, use_embedded)
        if kept:
            s["notices"].append("Kept your corrections on page " + ", ".join(map(str, kept)))
        st.rerun()

    result = s["results"].get(page_no)
    rotation = s["rotations"].get(page_no, 0)
    left, right = st.columns([0.9, 1.1], gap="large")
    with left:
        image = page_image(s["file_key"], page_no, PREVIEW_DPI, rotation, s["pdf_bytes"])
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
    count = s["page_count"]
    st.markdown(
        '<p class="fd-help">Turn pages the right way up and drop the ones you do not need. '
        "Nothing changes in your original file; the edits apply to what you export.</p>",
        unsafe_allow_html=True,
    )
    kept = count - len(s["removed"])
    per_row = 5
    for start in range(0, count, per_row):
        cols = st.columns(per_row, gap="medium")
        for col, page_no in zip(cols, range(start, min(start + per_row, count))):
            with col:
                rotation = s["rotations"].get(page_no, 0)
                gone = page_no in s["removed"]
                thumb = page_image(s["file_key"], page_no, THUMB_DPI, rotation, s["pdf_bytes"])
                st.image(P.dim_image(thumb) if gone else thumb, use_container_width=True)
                if gone:
                    st.markdown(f'<p class="fd-cap removed">Page {page_no + 1}, removed</p>', unsafe_allow_html=True)
                else:
                    turned = f", turned {rotation}°" if rotation else ""
                    st.markdown(f'<p class="fd-cap">Page {page_no + 1}{turned}</p>', unsafe_allow_html=True)
                b1, b2, b3 = st.columns([1, 1, 2])
                b1.button(":material/rotate_left:", key=f"l{page_no}", help="Turn left", disabled=gone,
                          on_click=rotate, args=(page_no, 270), use_container_width=True)
                b2.button(":material/rotate_right:", key=f"r{page_no}", help="Turn right", disabled=gone,
                          on_click=rotate, args=(page_no, 90), use_container_width=True)
                b3.button("Restore" if gone else "Remove", key=f"x{page_no}", on_click=toggle_removed,
                          args=(page_no,), use_container_width=True, disabled=not gone and kept <= 1,
                          help="The last remaining page cannot be removed." if not gone and kept <= 1 else None)

    st.markdown('<h2 class="fd-h2" style="margin-top:26px">Stamp a note on a page</h2>'
                '<p class="fd-help">A short visible line such as “Reviewed copy” or a reference number. '
                "Hindi works too.</p>", unsafe_allow_html=True)
    with st.form("note", clear_on_submit=True, border=False):
        n_text, n_page, n_pos, n_size, n_add = st.columns([3, 1, 1.4, 1, 1.1], vertical_alignment="bottom")
        text = n_text.text_input("Note", placeholder="Reviewed copy, 25 Nov 2026", max_chars=120)
        target = n_page.selectbox("Page", list(range(count)), format_func=lambda p: str(p + 1))
        position = n_pos.selectbox("Corner", P.NOTE_POSITIONS)
        size = n_size.number_input("Size", 8, 28, 12)
        if n_add.form_submit_button("Add note", use_container_width=True):
            if text.strip():
                s["notes"].append({"page": target, "text": text.strip(), "position": position, "size": int(size)})
            else:
                st.warning("Type the note's text first.")
    for i, note in enumerate(s["notes"]):
        line, drop = st.columns([6, 1], vertical_alignment="center")
        line.markdown(
            f'<div class="fd-status" style="margin:0">Page {note["page"] + 1}, {note["position"].lower()}: '
            f'{html.escape(note["text"])}</div>', unsafe_allow_html=True)
        if drop.button("Delete", key=f"note_{i}", use_container_width=True):
            s["notes"].pop(i)
            st.rerun()


def export_tab() -> None:
    s = st.session_state
    count = s["page_count"]
    kept = [p for p in range(count) if p not in s["removed"]]
    layer_pages = [p for p in kept if p in s["results"] and s["results"][p]["source"] == "ocr" and not is_stale(p)]
    stale = [p + 1 for p in kept if is_stale(p)]
    unread = [p + 1 for p in kept if p not in s["results"]]
    name = P.safe_filename(s["filename"])

    left, right = st.columns([1.1, 0.9], gap="large")
    with left:
        st.markdown('<h2 class="fd-h2">What your PDF will contain</h2>', unsafe_allow_html=True)
        turned = sum(1 for p in kept if s["rotations"].get(p, 0))
        noted = sum(1 for n in s["notes"] if n["page"] in kept)
        items = [f"{plural(len(kept), 'page')}" + (f" ({len(s['removed'])} removed)" if s["removed"] else "")]
        if turned:
            items.append(f"{plural(turned, 'page')} turned")
        if noted:
            items.append(f"{plural(noted, 'note')} stamped")
        if layer_pages:
            items.append(f"Searchable text on {plural(len(layer_pages), 'page')}, including your corrections")
        st.markdown('<ul class="fd-list">' + "".join(f"<li>{i}</li>" for i in items) + "</ul>", unsafe_allow_html=True)
        if unread and engine()["ok"]:
            st.markdown(
                f'<div class="fd-status warn" style="margin-top:12px">Not read yet: page {", ".join(map(str, unread))}. '
                "Those pages will not be searchable.</div>", unsafe_allow_html=True)
        if stale:
            st.markdown(
                f'<div class="fd-status warn" style="margin-top:12px">Rotated since reading: page '
                f'{", ".join(map(str, stale))}. Read them again to make them searchable.</div>',
                unsafe_allow_html=True)
    with right:
        st.markdown('<h2 class="fd-h2">Download</h2>', unsafe_allow_html=True)
        try:
            pdf, report = export_pdf(
                s["file_key"], dict(s["rotations"]), tuple(sorted(s["removed"])), list(s["notes"]),
                {p: s["results"][p] for p in layer_pages}, {p: s["texts"][p] for p in layer_pages if p in s["texts"]},
                bool(layer_pages), s["pdf_bytes"],
            )
        except Exception as exc:  # noqa: BLE001 - shown to the user
            st.error(f"The PDF could not be prepared. {exc}")
        else:
            label = "Download searchable PDF" if layer_pages else "Download PDF"
            st.download_button(label, pdf, file_name=f"{name}-{'searchable' if layer_pages else 'edited'}.pdf",
                               mime="application/pdf", type="primary", use_container_width=True)
            size = f"{len(pdf) / 1024:.0f} KB."
            st.markdown(
                f'<p class="fd-cap">{size} The page images are untouched; the text sits invisibly on top of them.</p>'
                if layer_pages else
                f'<p class="fd-cap">{size} Read the pages to make this PDF searchable.</p>',
                unsafe_allow_html=True)
            loose = [p + 1 for p, mode in report["layers"].items() if mode == "page"]
            if loose:
                st.markdown(
                    f'<div class="fd-status">On page {", ".join(map(str, sorted(loose)))} your corrections changed '
                    "the number of lines, so the text is searchable but will not line up exactly with the scan.</div>",
                    unsafe_allow_html=True)
        text = P.build_text(count, s["texts"], s["removed"])
        st.download_button("Download text (.txt)", text.encode("utf-8"), file_name=f"{name}.txt", mime="text/plain",
                           use_container_width=True, disabled=not text)
    st.markdown(
        '<p class="fd-help" style="margin-top:22px">Recognition is never perfect. Check names, amounts and dates '
        "against the page before relying on the text.</p>", unsafe_allow_html=True)


# --------------------------------------------------------------------------

if "pdf_bytes" not in st.session_state:
    landing()
else:
    top_bar()
    tab_read, tab_pages, tab_export = st.tabs(["Read and correct", "Arrange pages", "Export"])
    with tab_read:
        read_tab()
    with tab_pages:
        pages_tab()
    with tab_export:
        export_tab()
