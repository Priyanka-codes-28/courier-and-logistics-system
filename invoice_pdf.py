import io
import re
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from models import Payment
from timezone_utils import IST, format_ist

NAVY = colors.HexColor("#0B1F3A")
BLUE = colors.HexColor("#1B5FD1")
LIGHT = colors.HexColor("#F1F5FB")
LINE = colors.HexColor("#DCE5F2")
DIM = colors.HexColor("#5C6B7A")
NA = "Not available"

PAYMENT_STATUS_STYLE = {
    "paid": ("PAID", colors.HexColor("#15803D"), colors.HexColor("#E3F6EA")),
    "pending": ("PENDING", colors.HexColor("#C2570C"), colors.HexColor("#FFF0E0")),
}

_TRACKING_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


def is_valid_tracking_id(value):
    return bool(value) and bool(_TRACKING_RE.match(value))


def invoice_filename(tracking_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", tracking_id or "") or "shipment"
    return f"CourierOS_Invoice_{safe}.pdf"


def _txt(value, fallback=NA):
    """Escape user data for ReportLab's mini-markup; blank/None -> fallback."""
    if value is None or str(value).strip() == "":
        return escape(fallback)
    return escape(str(value).strip())


def _money(value):
    return f"INR {float(value):,.2f}"


def _when(value, with_time=True):
    if not value:
        return NA
    try:
        text = format_ist(value)
        if text:
            return str(text)
    except Exception:
        pass
    try:
        return value.strftime("%d %b %Y, %I:%M %p" if with_time else "%d %b %Y")
    except Exception:
        return str(value)


def _styles():
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13, textColor=NAVY)
    return {
        "base": base,
        "dim": ParagraphStyle("dim", parent=base, textColor=DIM, fontSize=8.5, leading=11),
        "label": ParagraphStyle("label", parent=base, textColor=DIM, fontSize=7.5, leading=10,
                                fontName="Helvetica-Bold"),
        "bold": ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold"),
        "right": ParagraphStyle("right", parent=base, alignment=2),
        "right_bold": ParagraphStyle("rb", parent=base, alignment=2, fontName="Helvetica-Bold"),
        "h_brand": ParagraphStyle("hb", parent=base, fontName="Helvetica-Bold", fontSize=22,
                                  leading=26, textColor=colors.white),
        "h_tag": ParagraphStyle("ht", parent=base, fontSize=8.5, textColor=colors.HexColor("#CFE0FF")),
        "h_inv": ParagraphStyle("hi", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22,
                                textColor=colors.white, alignment=2),
        "h_meta": ParagraphStyle("hm", parent=base, fontSize=8.5, textColor=colors.HexColor("#CFE0FF"),
                                 alignment=2),
        # -6 pt cancels the frame padding so headings line up with the full-width tables
        "section": ParagraphStyle("sec", parent=base, fontName="Helvetica-Bold", fontSize=10.5,
                                  textColor=BLUE, spaceBefore=10, spaceAfter=4, leftIndent=-6),
        "section_cell": ParagraphStyle("secc", parent=base, fontName="Helvetica-Bold", fontSize=10.5,
                                       textColor=BLUE, spaceBefore=10, spaceAfter=4),
        "note": ParagraphStyle("note", parent=base, textColor=DIM, fontSize=8.5, leading=11, leftIndent=-6),
        "th": ParagraphStyle("th", parent=base, fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.white),
        "th_r": ParagraphStyle("thr", parent=base, fontName="Helvetica-Bold", fontSize=8.5,
                               textColor=colors.white, alignment=2),
        "total": ParagraphStyle("tot", parent=base, fontName="Helvetica-Bold", fontSize=11),
        "total_r": ParagraphStyle("totr", parent=base, fontName="Helvetica-Bold", fontSize=11, alignment=2),
    }


def _kv(label, value, st):
    return [Paragraph(label.upper(), st["label"]), Paragraph(value, st["base"])]


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(DIM)
    canvas.drawString(18 * mm, 10 * mm,
                      "This is a computer-generated invoice and does not require a signature.")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_invoice_pdf(shipment, payment, sender=None, generated_at=None):
    st = _styles()
    generated_at = generated_at or datetime.now(IST)
    sender = sender or {}
    width = A4[0] - 36 * mm

    invoice_no = f"INV-{int(shipment['id']):06d}"
    story = []

    # header band 
    head = Table(
        [[
            [Paragraph("CourierOS", st["h_brand"]),
             Paragraph("Courier &amp; Logistics Management", st["h_tag"])],
            [Paragraph("INVOICE", st["h_inv"]),
             Paragraph(f"No. {escape(invoice_no)}", st["h_meta"]),
             Paragraph(f"Generated: {escape(_when(generated_at))}", st["h_meta"])],
        ]],
        colWidths=[width * 0.55, width * 0.45],
    )
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story += [head, Spacer(1, 8)]

    # reference strip
    ref = Table(
        [[
            [Paragraph("TRACKING ID", st["label"]), Paragraph(f"<b>{_txt(shipment.get('tracking_id'))}</b>", st["base"])],
            [Paragraph("SHIPMENT ID", st["label"]), Paragraph(_txt(shipment.get("id")), st["base"])],
            [Paragraph("SHIPMENT CREATED", st["label"]), Paragraph(escape(_when(shipment.get("created_at"))), st["base"])],
            [Paragraph("SHIPMENT STATUS", st["label"]), Paragraph(_txt(shipment.get("status")), st["base"])],
        ]],
        colWidths=[width * 0.28, width * 0.17, width * 0.30, width * 0.25],
    )
    ref.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(ref)

    # sender / receiver 
    sender_name = shipment.get("sender_name") or sender.get("name")
    sender_phone = shipment.get("sender_phone") or sender.get("phone")
    sender_email = sender.get("email")

    def party(title, lines):
        cells = [Paragraph(title, st["section_cell"])]
        for label, value in lines:
            cells += [Paragraph(label.upper(), st["label"]), Paragraph(value, st["base"]), Spacer(1, 3)]
        return cells

    parties = Table(
        [[
            party("Sender", [("Name", _txt(sender_name)), ("Phone", _txt(sender_phone)),
                             ("Email", _txt(sender_email)),
                             ("Pickup address", _txt(shipment.get("sender_address")))]),
            party("Receiver", [("Name", _txt(shipment.get("receiver_name"))),
                               ("Phone", _txt(shipment.get("receiver_phone"))),
                               ("Delivery address", _txt(shipment.get("receiver_address")))]),
        ]],
        colWidths=[width * 0.5, width * 0.5],
    )
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(parties)

    # shipment details
    weight = shipment.get("weight")
    weight_txt = f"{float(weight):g} kg" if weight else "Not specified"
    details = [
        _kv("Package type", _txt(shipment.get("package_type")), st),
        _kv("Weight", escape(weight_txt), st),
    ]
    if shipment.get("package_description"):
        details.append(_kv("Description", _txt(shipment.get("package_description")), st))
    det = Table(details, colWidths=[width * 0.25, width * 0.75])
    det.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [Paragraph("Shipment details", st["section"]), det]

    # charges
    story.append(Paragraph("Charges", st["section"]))
    rows = [[Paragraph("Description", st["th"]), Paragraph("Rate", st["th_r"]),
             Paragraph("Qty", st["th_r"]), Paragraph("Amount", st["th_r"])]]
    if payment is not None and payment.get("amount") is not None:
        total = float(payment["amount"])
        effective_weight = float(weight) if weight else 0.5
        breakdown_matches = abs(Payment.calculate_fee(weight) - total) < 0.005
        if breakdown_matches:
            rows.append([Paragraph("Base delivery charge", st["base"]), Paragraph("-", st["right"]),
                         Paragraph("1", st["right"]), Paragraph(_money(Payment.FLAT_RATE), st["right"])])
            weight_label = "Weight charge (price per kg)"
            if not weight:
                weight_label += " - default 0.5 kg applied, no weight recorded"
            rows.append([Paragraph(weight_label, st["base"]),
                         Paragraph(f"{_money(Payment.PER_KG_RATE)} / kg", st["right"]),
                         Paragraph(f"{effective_weight:g} kg", st["right"]),
                         Paragraph(_money(effective_weight * Payment.PER_KG_RATE), st["right"])])
        else:
            rows.append([Paragraph("Delivery charges (as billed)", st["base"]), Paragraph("-", st["right"]),
                         Paragraph("-", st["right"]), Paragraph(_money(total), st["right"])])
        rows.append([Paragraph("Total shipment charges", st["total"]), "", "",
                     Paragraph(_money(total), st["total_r"])])
    else:
        rows.append([Paragraph("No payment record exists for this shipment, so charges are not available.",
                               st["dim"]), "", "", Paragraph(NA, st["right"])])
        rows.append([Paragraph("Total shipment charges", st["total"]), "", "",
                     Paragraph(NA, st["total_r"])])

    charges = Table(rows, colWidths=[width * 0.52, width * 0.18, width * 0.12, width * 0.18], repeatRows=1)
    charges.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, LINE),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("SPAN", (0, -1), (2, -1)),
    ]))
    story.append(charges)

    # payment
    story.append(Paragraph("Payment", st["section"]))
    if payment is not None:
        raw_status = (payment.get("status") or "").strip().lower()
        label, fg, bg = PAYMENT_STATUS_STYLE.get(
            raw_status, ((raw_status or "unknown").upper(), DIM, LIGHT))
        status_cell = Table([[Paragraph(f"<b>{escape(label)}</b>", ParagraphStyle(
            "ps", parent=st["base"], textColor=fg, alignment=1))]], colWidths=[28 * mm])
        status_cell.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.6, fg),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        pay_rows = [
            [Paragraph("PAYMENT STATUS", st["label"]), status_cell],
            _kv("Payment method", _txt(payment.get("payment_method")), st),
            _kv("Transaction reference", _txt(payment.get("transaction_ref")), st),
        ]
        if raw_status == "paid":
            pay_rows.append(_kv("Paid on", escape(_when(payment.get("paid_at"))), st))
    else:
        pay_rows = [
            _kv("Payment status", "No payment record", st),
            _kv("Payment method", escape(NA), st),
            _kv("Transaction reference", escape(NA), st),
        ]
    pay = Table(pay_rows, colWidths=[width * 0.25, width * 0.75])
    pay.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [pay, Spacer(1, 14), HRFlowable(width="100%", thickness=0.6, color=LINE), Spacer(1, 4),
              Paragraph("Thank you for shipping with CourierOS. Payment status shown above reflects "
                        "our records at the time this invoice was generated.", st["note"])]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm, bottomMargin=18 * mm,
        title=f"CourierOS Invoice {invoice_no}", author="CourierOS",
        subject=f"Invoice for shipment {shipment.get('tracking_id')}",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()