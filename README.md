# FolioDesk — PDF + OCR POC

A CPU-based, single-service Streamlit proof of concept for scanned PDFs.

## Included
- PDF upload and validation (10 MB / 10 pages)
- Page preview
- Tesseract OCR on CPU, page by page
- Hindi, English, and Hindi + English recognition
- French, German, and Spanish selectors (requires matching Tesseract language data on the host)
- Editable OCR text by page
- TXT export
- Searchable PDF export with an invisible text layer
- Basic PDF operations: rotate pages, remove pages, and add a simple text note
- No database, login, payment gateway, external AI API, or permanent file storage

## Local setup

Requires Python 3.11 and Tesseract with English/Hindi language packs.

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin fonts-noto-core fonts-dejavu-core
```

Then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Render deployment

The repository includes `render.yaml`, `apt.txt`, and `runtime.txt` for a Render web service. The configured start command binds Streamlit to `0.0.0.0:$PORT`; the health check is `/_stcore/health`.

## Limitations
- CPU-only OCR; speed depends on scan quality, page dimensions, and available CPU.
- OCR is not guaranteed to be accurate. Review important content.
- Searchable PDF export adds a page-level invisible text layer; it does not reconstruct original layout or provide true in-place editing.
- The PDF editor is limited to rotation, page deletion, and adding a simple text note.
- Uploaded documents are processed in memory. Render's filesystem is ephemeral; do not upload confidential documents to a public demo without a privacy review.
- This is a demonstration build, not a production multi-user service. It has no authentication, quotas, malware scanning, job queue, or durable storage.

## Licensing
Review the licenses and obligations of Streamlit, PyMuPDF/MuPDF (AGPL/commercial licensing terms), Tesseract, and its language data before commercial distribution.
