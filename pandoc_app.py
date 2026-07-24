"""
pandoc_app.py
─────────────
FastAPI app mounting:
  - /pandoc  → Pandoc DOCX pipeline
  - /pdf     → PDF parsing pipeline (pdfplumber + Textract)
  - /excel   → Excel / CSV extraction pipeline

Run:
    uvicorn pandoc_app:app --host 0.0.0.0 --port 8001
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pandoc_pipeline_router import router as pandoc_router
from pdf_parsing import router as pdf_router
from excel_parsing import router as excel_router

app = FastAPI(
    title="Document Extraction Pipeline",
    description="DOCX → Markdown (Pandoc) + PDF extraction (pdfplumber / Textract) + Excel / CSV extraction.",
    version="1.2.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pandoc_router, prefix="/pandoc", tags=["Pandoc Pipeline"])
app.include_router(pdf_router,   prefix="/pdf",    tags=["PDF Pipeline"])
app.include_router(excel_router, prefix="/excel",  tags=["Excel Pipeline"])

@app.get("/health")
def health():
    return {"status": "ok", "engines": ["pandoc", "pdf", "excel"]}
