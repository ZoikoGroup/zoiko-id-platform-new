# OCR sidecar (CR-073 P2-4): Tesseract plus Pillow behind a tiny HTTP service. Used only through the document extraction route.
# Build:  docker compose --profile ocr build ocr        Run:  docker compose --profile ocr up -d ocr
FROM python:3.13-slim

# Tesseract and the language packs the service allows (eng, fra, spa, deu, ara). Nothing else is installed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra tesseract-ocr-spa tesseract-ocr-deu tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

# Pillow crops regions; it is pinned, and it is the only Python dependency.
RUN pip install --no-cache-dir "pillow==12.3.0"

COPY server.py /app/server.py

# no root, no shell login, nothing writable but /tmp (compose mounts it as a tmpfs)
RUN useradd --system --no-create-home --shell /usr/sbin/nologin ocr
USER ocr
WORKDIR /app
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4).status == 200 else 1)"
CMD ["python", "/app/server.py"]
