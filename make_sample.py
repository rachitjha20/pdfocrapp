"""Regenerate samples/sample-scan.pdf: a two-page, image-only "scan" for demos and tests.

Run with:  python make_sample.py
The content is fictional. Pages are typeset, rasterised, then roughened so they
behave like a real scan (no text layer, slight skew, noise; page 2 is sideways).
"""
from __future__ import annotations

import io
import random
from pathlib import Path

import fitz
from PIL import Image, ImageFilter

PAGES = [
    """
    <h2>Sahyog Housing Society</h2>
    <p><b>Notice of the annual general meeting</b></p>
    <p>Ref. SHS/2026/114 &nbsp;&nbsp; Date: 12 November 2026</p>
    <p>All members are informed that the annual general meeting of the society
    will be held on Sunday, 6 December 2026 at 10:30 am in the community hall
    on the ground floor of B wing.</p>
    <p>Agenda</p>
    <p>1. Approval of the minutes of the previous meeting.<br>
    2. Adoption of the audited accounts for the year 2025-26.<br>
    3. Repair of the terrace waterproofing, estimated cost Rs 4,85,000.<br>
    4. Any other matter with the permission of the chair.</p>
    <p>सभी सदस्यों को सूचित किया जाता है कि समिति की वार्षिक आम सभा रविवार,
    6 दिसंबर 2026 को सुबह 10:30 बजे सामुदायिक भवन में होगी।</p>
    <p>कृपया समय पर उपस्थित रहें और अपना पहचान पत्र साथ लाएँ।</p>
    <p>Secretary<br>Sahyog Housing Society, Pune 411038</p>
    """,
    """
    <h2>Maintenance statement</h2>
    <p>Quarter ending 30 September 2026</p>
    <p>Opening balance: Rs 2,14,600<br>
    Collections received: Rs 3,92,250<br>
    Electricity and water: Rs 1,08,430<br>
    Security and housekeeping: Rs 1,61,000<br>
    Lift servicing contract: Rs 36,500<br>
    Closing balance: Rs 3,00,920</p>
    <p>Members with dues older than ninety days are requested to clear them
    before the meeting.</p>
    <p>बकाया राशि वाले सदस्य बैठक से पहले भुगतान करें।</p>
    """,
]

CSS = "body{font-family:sans-serif;font-size:13pt;line-height:1.5} h2{font-size:20pt}"


def scan(page: fitz.Page, rng: random.Random, sideways: bool) -> bytes:
    pix = page.get_pixmap(dpi=170, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    img = img.rotate(rng.uniform(-0.7, 0.7), resample=Image.BICUBIC, fillcolor=255)
    noise = Image.effect_noise(img.size, 14)
    img = Image.blend(img, noise, 0.12).filter(ImageFilter.GaussianBlur(0.5))
    img = img.point(lambda v: min(255, int(v * 1.08 + 6)))
    if sideways:
        img = img.rotate(90, expand=True)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def main() -> None:
    rng = random.Random(7)
    typeset = fitz.open()
    for html in PAGES:
        page = typeset.new_page(width=595, height=842)
        page.insert_htmlbox(fitz.Rect(64, 64, 531, 778), html, css=CSS)
    out = fitz.open()
    for i, page in enumerate(typeset):
        sideways = i == 1
        w, h = (842, 595) if sideways else (595, 842)
        out.new_page(width=w, height=h).insert_image(fitz.Rect(0, 0, w, h), stream=scan(page, rng, sideways))
    target = Path(__file__).parent / "samples" / "sample-scan.pdf"
    target.parent.mkdir(exist_ok=True)
    out.save(target, garbage=3, deflate=True)
    print(f"Wrote {target} ({target.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
