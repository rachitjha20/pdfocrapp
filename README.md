# FolioDesk — PDF editor + OCR POC

A CPU-based, single-service Streamlit proof of concept: edit PDFs in the browser, and make scanned ones searchable.

## Included

**Editor**

- A page you click and drag on, a tool palette beside it, and a panel that shows the selected object's properties and updates the page as you change them
- Select anything you added to restyle it, drag it to move it, set its exact position and size, duplicate it, reorder it front to back, or delete it
- Undo and redo for every change to the document, including page changes
- Edit text: click a line to change its wording in place. On a normal PDF the original text is removed and rewritten in a matching size, colour and style; on a scan (after reading it) the old wording is painted over in the paper's colour
- Find and replace across one page or all pages
- Add text with a choice of font, size, colour, bold, italic and alignment (English or Hindi)
- Highlight, underline and strike out: drag, click a line, or find words
- Rectangles, ellipses, lines and arrows with colour, thickness, fill and opacity
- Images, with an option to remove a white background so a photographed signature sits on the page as ink
- Rubber stamps (APPROVED, PAID, DRAFT … or your own wording)
- Comments: sticky notes that PDF readers show as an icon with pop-up text
- Cover up an area, or redact it. Redaction deletes the content underneath, including the pixels of a scanned image; redacted pages get no hidden text layer
- Fill in PDF forms: text fields, tick boxes and drop-downs

**Pages**

- Live thumbnails: reorder, rotate, duplicate, remove/restore, add a blank page, add pages from another PDF
- Opens password-protected PDFs when given the password

**Reading scans (OCR)**

- Tesseract OCR on CPU, pages read in parallel; sideways pages are detected and turned upright first
- Pages that already contain real text are copied exactly instead of being re-recognised
- Words the engine was unsure about are highlighted on the page preview
- Editable text per page; corrections are kept when you re-read the other pages
- Language list shows only the Tesseract language packs actually installed

**Export**

- PDF with all edits applied, and searchable text placed word by word over scanned pages so search hits and selections line up with the image
- Finishing options: page numbers, watermark, smaller file size, password protection
- Extra downloads: each page as a PNG or as its own PDF (zipped), and the text as TXT

Limits: 10 MB and 10 pages per document. No database, login, payment gateway, external AI API or permanent file storage.

## Known limits of the editor

- The page updates when you release the mouse, not while you drag, and there are no resize handles: resize with the numbers in the panel
- Edited text is rewritten in a built-in sans, serif or typewriter face, not the document's own font, and always on one line
- No freehand drawing
- Hindi text is placed without complex shaping, so some conjuncts may not join as they would in print

## Code layout

- `app.py`: the Streamlit interface only
- `processing.py`: validation, rendering, OCR and PDF export; no Streamlit imports, so it can be reused or tested alone
- `tests/test_processing.py`: end-to-end checks against `samples/sample-scan.pdf` (`pip install pytest && pytest`)
- `make_sample.py`: regenerates the sample scan
- `.streamlit/config.toml`: theme and the 10 MB upload cap

`OCR_WORKERS` sets how many pages are read at once (default: up to 2). `render.yaml` sets it to 1 for small instances.

## Local setup

Requirements: Python 3.11 and the Tesseract executable with English/Hindi language packs.

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin tesseract-ocr-osd fonts-noto-core fonts-dejavu-core
```

### Windows

Install Tesseract OCR and the `eng` and `hin` traineddata files. If Tesseract is not on PATH, set `pytesseract.pytesseract.tesseract_cmd` in `app.py` to the installed executable path.

Then:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Run with Docker

The image includes Tesseract, the English, Hindi and orientation data, and the fonts, so nothing else needs installing:

```bash
docker build -t foliodesk .
docker run --rm -p 8501:10000 foliodesk
```

Then open http://localhost:8501.

## Deploy to Render

Render's native Python runtime cannot install Tesseract (it ignores `apt.txt`), so the app deploys as a Docker service.

1. Push this folder to a GitHub repository.
2. In Render, create **New → Web Service**, connect the repository and choose **Docker** as the language. Render finds the `Dockerfile` and needs no build or start command. (Or use **New → Blueprint**, which reads `render.yaml`.)
3. Select the Free instance for a short demo if available.
4. The Streamlit health endpoint is `/_stcore/health`.

An existing service created with the Python runtime cannot be switched to Docker: create a new service and delete the old one. If the app says "Text recognition is unavailable", it is running without Tesseract, which means it was not built from the Dockerfile.

## Today's demo / keep-awake helper

For the 25 Nov 2026 demo only, you can run the optional local helper from your own computer. It sends a health request at a random 8–12 minute interval and stops after the demo date. Keep the terminal open while you need the demo awake:

```bash
python keep_awake_today.py
```

Paste your Render health URL when prompted. The helper uses only Python's standard library. You can also use an external uptime monitor to check:

`https://YOUR-SERVICE.onrender.com/_stcore/health`

Do not rely on a self-ping process inside the same sleeping service: once the service has spun down, its own process cannot wake itself. Render free instances may still spin down or have cold starts; periodic requests are not a guaranteed always-on service.

## Limitations to know

- CPU-only OCR; speed depends on scan quality, page dimensions and Render CPU allocation.
- Hindi/English language data are installed by the `Dockerfile`. French, German and Spanish appear in the language list once `tesseract-ocr-fra`, `tesseract-ocr-deu` or `tesseract-ocr-spa` is added to its `apt-get install` line.
- OCR is not guaranteed to be accurate. Review important content.
- The searchable text layer is for search and copy. Hindi text is stored without complex-script shaping, and if a correction changes the number of lines on a page, that page's text is searchable but no longer lined up with the scan.
- "Hindi + English" runs Tesseract as `eng+hin`: with Hindi first, the digit 1 is dropped from Latin text.
- The editor is not a full layout editor. "Replace text" covers the old words with the surrounding paper colour and writes the new ones in a standard font sized to fit, so it suits short pieces such as a number, date or name, and the result will not match unusual typefaces.
- After redacting, the recognised text shown under Read and correct (and the .txt download) can still contain the redacted words until you delete them there. The app warns about this.
- Areas are marked by dragging on the page; the outline appears when you release the mouse, not while dragging.
- Uploaded documents are processed in memory. Render's filesystem is ephemeral, but do not upload confidential documents to a public demo without an appropriate privacy review.
- This is a demonstration build, not a production multi-user service. It has no authentication, quotas, malware scanning, job queue or durable storage.

## Licensing

Review the licenses and obligations of Streamlit, PyMuPDF/MuPDF (AGPL/commercial licensing terms), Tesseract and its language data before commercial distribution.
