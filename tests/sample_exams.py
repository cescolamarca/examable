"""Synthetic exam PDFs in the layout used by the University of Salerno networking course.

The two sessions share two multiple-choice questions on purpose: in session B they
appear under a different number and with the answer options shuffled, which is the
case the deduplication fingerprint has to collapse.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

HEADER = [
    "Universita degli Studi di Salerno - Reti di Calcolatori",
    "Cognome ______ Nome ______ Matricola ______",
]

SESSION_A = [
    *HEADER,
    "DOMANDA 1",
    "Quale protocollo del livello di trasporto offre un servizio orientato alla connessione?",
    "a. UDP",
    "b. TCP",
    "c. IP",
    "d. ICMP",
    "DOMANDA 2",
    "Quanti bit compongono un indirizzo IPv4?",
    "a. sedici",
    "b. trentadue",
    "c. sessantaquattro",
    "d. centoventotto",
    "DOMANDA 3",
    "Quale record DNS associa un nome di dominio a un indirizzo IPv4?",
    "a. MX",
    "b. CNAME",
    "c. A",
    "d. NS",
    "DOMANDA TEORIA",
    "Descrivere il meccanismo di controllo della congestione di TCP.",
    "ESERCIZIO 1",
    "Un router deve inoltrare un datagramma di 4000 byte su un link con MTU di 1500 byte.",
    "1) Calcolare il numero di frammenti generati.",
    "2) Indicare offset e flag di ciascun frammento.",
]

SESSION_B = [
    *HEADER,
    "DOMANDA 1",
    "Quanti bit compongono un indirizzo IPv4?",
    "a. trentadue",
    "b. centoventotto",
    "c. sedici",
    "d. sessantaquattro",
    "DOMANDA 2",
    "Quale meccanismo permette a piu host di una rete privata di condividere un indirizzo pubblico?",
    "a. DHCP",
    "b. NAT",
    "c. ARP",
    "d. DNS",
    "DOMANDA 3",
    "Quale protocollo del livello di trasporto offre un servizio orientato alla connessione?",
    "a. TCP",
    "b. ICMP",
    "c. UDP",
    "d. IP",
    "DOMANDA TEORIA",
    "Spiegare la differenza tra commutazione di circuito e commutazione di pacchetto.",
]


def write_pdf(path: Path, lines: list[str]) -> Path:
    """Render one text line per row, starting a new page when the current one is full."""
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    y = height - 60
    for line in lines:
        if y < 60:
            pdf.showPage()
            y = height - 60
        pdf.setFont("Helvetica", 10)
        pdf.drawString(50, y, line)
        y -= 16
    pdf.save()
    return path


def write_sample_exams(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    return (
        write_pdf(directory / "esame_2024_06_A.pdf", SESSION_A),
        write_pdf(directory / "esame_2024_09_B.pdf", SESSION_B),
    )


def write_scanned_pdf(path: Path, lines: list[str]) -> Path:
    """An image-only PDF, like a scanned exam: render the text PDF and embed the bitmap.

    Requires `pdftoppm` (poppler-utils).
    """
    with tempfile.TemporaryDirectory() as tmp:
        text_pdf = write_pdf(Path(tmp) / "text.pdf", lines)
        image_base = Path(tmp) / "page"
        subprocess.run(
            ["pdftoppm", "-r", "200", "-png", "-singlefile", str(text_pdf), str(image_base)],
            check=True,
            capture_output=True,
        )
        pdf = canvas.Canvas(str(path), pagesize=A4)
        width, height = A4
        pdf.drawImage(ImageReader(str(image_base.with_suffix(".png"))), 0, 0, width=width, height=height)
        pdf.save()
    return path
