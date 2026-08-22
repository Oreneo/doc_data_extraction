"""
Regenerate the signature test fixtures.

Produces two PDFs with byte-for-byte identical *text* that differ only in
whether a handwritten-style curve sits over the customer's signature rule.
That isolation is the point: text extraction cannot tell them apart, so any
test that distinguishes them is exercising the geometric scan and nothing
else.

The generated PDFs are committed, so running this is only necessary when
changing the fixtures. It needs reportlab, which is NOT a project
dependency:

    pip install reportlab
    python tests/fixtures/generate_fixtures.py
"""

import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))

# Two signature columns: vendor on the left, customer on the right. The
# signature is drawn only in the customer's column, so the fixture also
# exercises the check that a band must not bleed across columns.
VENDOR_X = 72
CUSTOMER_X = 320


def build(path, signed):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica", 11)

    y = 700
    for line in [
        "Order Form", "", "Customer Billing Company Name: Testco Inc.",
        "Bill To: 1 Test Street, Testville", "Start Date: January 1, 2025",
        "End Date: December 31, 2025", "Payment Terms: Net 30",
        "Total fee: $10,000", "", "Order Details",
        "Name  Start Date  End Date  Rate  Quantity  Total amount",
        "Widget  Jan 1,2025  Dec 31,2025  $10,000  1  $10,000",
    ]:
        c.drawString(72, y, line)
        y -= 18

    y -= 40
    c.drawString(VENDOR_X, y, "Vendor Co")
    c.drawString(CUSTOMER_X, y, "Testco Inc.")
    c.drawString(VENDOR_X, y - 30, "Signature:")
    c.drawString(CUSTOMER_X, y - 30, "Signature:")

    # Signature rules. Drawn as lines, which the detector deliberately
    # ignores - a rule is present whether or not anyone signed on it.
    c.line(79, y - 80, 208, y - 80)
    c.line(CUSTOMER_X, y - 80, CUSTOMER_X + 129, y - 80)

    if signed:
        # Bezier curves, which is what a pen stroke or a pasted signature
        # image looks like to pdfplumber.
        path_obj = c.beginPath()
        path_obj.moveTo(330, y - 78)
        path_obj.curveTo(350, y - 55, 370, y - 95, 390, y - 68)
        path_obj.curveTo(405, y - 50, 420, y - 85, 440, y - 72)
        c.setLineWidth(1.2)
        c.drawPath(path_obj)

    c.drawString(VENDOR_X, y - 100, "Name: Alice Vendor")
    c.drawString(CUSTOMER_X, y - 100, "Name: Bob Customer")
    c.drawString(VENDOR_X, y - 120, "Title: CFO")
    c.drawString(CUSTOMER_X, y - 120, "Title: CEO")
    c.save()
    print(f"wrote {path}")


if __name__ == "__main__":
    build(os.path.join(HERE, "signed_order_form.pdf"), signed=True)
    build(os.path.join(HERE, "unsigned_order_form.pdf"), signed=False)
