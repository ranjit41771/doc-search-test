FROM python:3.12-slim

WORKDIR /app

# Install system dependencies:
#   tesseract-ocr       — OCR engine
#   tesseract-ocr-eng   — English language pack
#   tesseract-ocr-osd   — Orientation and script detection
#   poppler-utils       — PDF rendering for pdf2image (pdftoppm)
#   libmagic1           — MIME type detection for python-magic
#   libgl1              — OpenGL for Pillow on some systems
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-osd \
    poppler-utils \
    libmagic1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Run as a non-root user to limit blast radius if the container is compromised.
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
