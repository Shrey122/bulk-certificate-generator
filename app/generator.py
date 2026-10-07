import os
from datetime import date

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

GOLD, NAVY, GREY = HexColor("#B8860B"), HexColor("#1F2A44"), HexColor("#555555")


def output_dir():
    return os.environ.get("CERTGEN_OUTPUT_DIR", "certificates")


def _fit_font(text, font, size, max_width):
    """Shrink font size until the text fits on the page (long names)."""
    while size > 14 and stringWidth(text, font, size) > max_width:
        size -= 2
    return size


def generate_certificate(cert_id, name, event_name, issue_date=None):
    """Create one PDF from the fixed template and return its file path."""
    os.makedirs(output_dir(), exist_ok=True)
    path = os.path.join(output_dir(), f"certificate_{cert_id}.pdf")  # id, not name: no unsafe filenames
    issue_date = issue_date or date.today().strftime("%d %B %Y")

    w, h = landscape(A4)
    c = canvas.Canvas(path, pagesize=(w, h))
    try:
        # double border
        c.setStrokeColor(GOLD)
        c.setLineWidth(6)
        c.rect(25, 25, w - 50, h - 50)
        c.setLineWidth(1.5)
        c.rect(38, 38, w - 76, h - 76)

        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 40)
        c.drawCentredString(w / 2, h - 130, "CERTIFICATE OF COMPLETION")

        c.setFillColor(GREY)
        c.setFont("Helvetica", 16)
        c.drawCentredString(w / 2, h - 190, "This is proudly presented to")

        c.setFillColor(NAVY)
        size = _fit_font(name, "Helvetica-Bold", 38, w - 160)
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(w / 2, h - 255, name)
        c.setStrokeColor(GOLD)
        c.line(w / 2 - 200, h - 270, w / 2 + 200, h - 270)

        c.setFillColor(GREY)
        c.setFont("Helvetica", 16)
        c.drawCentredString(w / 2, h - 315, "for successfully completing")

        c.setFillColor(NAVY)
        size = _fit_font(event_name, "Helvetica-Bold", 24, w - 160)
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(w / 2, h - 355, event_name)

        c.setFillColor(GREY)
        c.setFont("Helvetica", 12)
        c.drawString(80, 80, f"Date: {issue_date}")
        c.drawRightString(w - 80, 80, f"Certificate ID: {cert_id:06d}")

        c.showPage()
        c.save()
    except Exception:
        if os.path.exists(path):  # don't leave half-written files behind
            os.remove(path)
        raise
    return path