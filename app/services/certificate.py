"""Certificate rendering (PDF via ReportLab) using ONE predefined template."""
import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f2a44")
GOLD = colors.HexColor("#b8860b")


def _fit_font_size(text: str, font: str, max_size: int, max_width: float) -> float:
    """Shrink the font until the text fits within max_width (long names)."""
    size = float(max_size)
    while size > 12 and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(
    *,
    path: Path,
    recipient_name: str,
    event_name: str,
    issuer_name: str,
    issue_date: date,
    certificate_id: str,
) -> None:
    """Render the certificate PDF to `path`. Written atomically (tmp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate - {recipient_name}")
    cx = PAGE_W / 2
    max_text_w = PAGE_W - 140

    # Double border
    c.setStrokeColor(GOLD)
    c.setLineWidth(4)
    c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
    c.setLineWidth(1)
    c.rect(35, 35, PAGE_W - 70, PAGE_H - 70)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 38)
    c.drawCentredString(cx, PAGE_H - 120, "CERTIFICATE OF COMPLETION")

    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 175, "This is to certify that")

    name_font = "Helvetica-BoldOblique"
    name_size = _fit_font_size(recipient_name, name_font, 44, max_text_w)
    c.setFillColor(GOLD)
    c.setFont(name_font, name_size)
    c.drawCentredString(cx, PAGE_H - 245, recipient_name)
    c.setStrokeColor(NAVY)
    c.line(cx - 220, PAGE_H - 257, cx + 220, PAGE_H - 257)

    c.setFillColor(NAVY)
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 295, "has successfully participated in")

    event_size = _fit_font_size(event_name, "Helvetica-Bold", 26, max_text_w)
    c.setFont("Helvetica-Bold", event_size)
    c.drawCentredString(cx, PAGE_H - 335, event_name)

    c.setFont("Helvetica", 13)
    c.drawString(80, 90, f"Issued by: {issuer_name}")
    c.drawRightString(PAGE_W - 80, 90, f"Date: {issue_date.strftime('%d %B %Y')}")
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.grey)
    c.drawCentredString(cx, 55, f"Certificate ID: {certificate_id}")

    c.showPage()
    c.save()
    os.replace(tmp_path, path)
