"""Regenerate the sample exam PDFs in examples/ (requires requirements-dev.txt and poppler-utils)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.sample_exams import SESSION_A, SESSION_B, SESSION_C, write_pdf, write_scanned_pdf


def main() -> None:
    out = PROJECT_ROOT / "examples"
    out.mkdir(exist_ok=True)
    write_pdf(out / "reti_2024_06_appello_A.pdf", SESSION_A)
    write_pdf(out / "reti_2024_09_appello_B.pdf", SESSION_B)
    write_scanned_pdf(out / "reti_2023_02_scansione.pdf", SESSION_C)
    for pdf in sorted(out.glob("*.pdf")):
        print(f"{pdf.relative_to(PROJECT_ROOT)}  {pdf.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
