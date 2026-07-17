# ─────────────────────────────────────────────────────────────────────────────
# Lean DOCX → Markdown pipeline using Pandoc + LibreOffice Writer (minimal)
# libreoffice-writer: handles .doc → .docx headless conversion only
# Expected image size: ~400-500 MB (smaller than full libreoffice suite)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Output goes to /tmp — ephemeral, consistent with in-memory job store
    DOCX_OUTPUT_DIR=/tmp/uploads

# System packages:
#   pandoc              — core .docx → Markdown conversion engine
#   libreoffice-writer  — minimal Writer-only install for .doc → .docx headless conversion
#   libglib2.0-0        — required shared lib for LibreOffice
RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    libreoffice-writer \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install only what pandoc_pipeline_router.py actually needs
RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    python-multipart \
    python-docx \
    pypandoc \
    boto3 \
    pillow \
    python-dotenv

# Copy only the files needed — not the heavy routers
COPY pandoc_pipeline_router.py .
COPY pandoc_app.py .

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

CMD ["uvicorn", "pandoc_app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
