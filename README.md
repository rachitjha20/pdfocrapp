# FolioDesk — PDF + OCR POC

A CPU-based, single-service Streamlit proof of concept for scanned PDFs.

## Included

- PDF upload and validation (10 MB / 10 pages)
- Page preview
- Tesseract OCR on CPU, page-by-page
- Hindi, English and Hindi + English selection
- French, German and Spanish selectors (requires corresponding Tesseract language data on the host)
- Editable OCR text by page
- TXT export
- Searchable PDF export with an invisible text layer
- Basic PDF operations: rotate page, remove page, add a simple text note
- No database, login, payment gateway, external AI API or permanent file storage

## Local setup

Requirements: Python 3.11 and the Tesseract executable with English/Hindi language packs.

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin fonts-noto-core fonts-dejavu-core
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

## Deploy to Render

1. Push this folder to a GitHub repository.
2. In Render, create **New → Web Service** and connect the repository.
3. Runtime: Python 3.
4. Build command: `pip install -r requirements.txt`
5. Start command:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true
```

6. Select the Free instance for a short demo if available. Render detects `apt.txt` and installs the Tesseract packages during build.
7. The Streamlit health endpoint is `/_stcore/health`.

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
- Hindi/English language data are installed by `apt.txt`. French/German/Spanish choices need `tesseract-ocr-fra`, `tesseract-ocr-deu`, or `tesseract-ocr-spa` added to `apt.txt`.
- OCR is not guaranteed to be accurate. Review important content.
- Searchable PDF export adds a page-level invisible text layer. It does not reconstruct the original text layout or provide true in-place editing of existing PDF text.
- The PDF editor is intentionally limited to rotation, page deletion and adding a simple text note.
- Uploaded documents are processed in memory. Render's filesystem is ephemeral, but do not upload confidential documents to a public demo without an appropriate privacy review.
- This is a demonstration build, not a production multi-user service. It has no authentication, quotas, malware scanning, job queue or durable storage.

## Licensing

Review the licenses and obligations of Streamlit, PyMuPDF/MuPDF (AGPL/commercial licensing terms), Tesseract and its language data before commercial distribution.
