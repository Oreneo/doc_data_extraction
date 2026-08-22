"""
Detect drawn or scanned signatures that text extraction cannot see.

`pdfplumber.extract_text()` returns nothing for image and vector content, so
a contract signed by hand or with a pasted signature image reads as unsigned
- a false negative, and the dangerous direction of error for a contracts
pipeline.

This closes that gap without OCR. It never tries to *read* a signature; it
only asks whether any non-text ink sits where a signature belongs, and
reports that as evidence for the extraction to weigh.
"""

import re
from typing import List, Optional

import pdfplumber
from pydantic import BaseModel

# Words that mark a signature area. The checkmark documents in sample_docs/
# use "Buyer Signature: ✔ Signed", others use a bare "Signature:".
SIGNATURE_WORD = re.compile(r"signature|signed", re.IGNORECASE)

# Annotation subtypes that can carry a signature.
#
# This is the case that matters most in practice: PDF viewers add signatures
# as ANNOTATIONS rather than page content. macOS Preview's Markup > Signature
# writes a /Stamp (for a drawn or scanned signature) or a /FreeText (for a
# typed one), and neither appears in page.images, page.curves, *or*
# extract_text(). A document signed that way is byte-different from the
# original yet produces identical text - so annotations have to be checked
# or every such signature is missed.
#
# /Link and /Popup are excluded: they are navigation and UI chrome, and a
# link sitting near a signature block would otherwise read as a signature.
SIGNATURE_ANNOTATION_SUBTYPES = {"Ink", "Stamp", "FreeText", "Widget"}

# How far around a signature label to look for ink, in PDF points.
#
# Layouts differ in whether the mark sits above the label (over a rule) or
# below it, so the band covers both. These defaults were calibrated against
# the unsigned documents in sample_docs/ - wide enough to find anchors in
# all four, tight enough that ACME's letterhead logo (top=-6..131) never
# falls inside one. Re-tune against genuinely signed documents.
BAND_ABOVE = 55
BAND_BELOW = 65
BAND_RIGHT = 260


class SignatureInkFinding(BaseModel):
    """
    What the geometric scan saw near the signature area(s) of a document.
    """

    found: bool = False
    # Human-readable account of what was found and where, suitable for
    # putting in front of the model and for storing as evidence.
    detail: Optional[str] = None
    anchors_examined: int = 0

    def as_prompt_hint(self) -> str:
        """
        Render the finding as a line for the extraction prompt.

        The model cannot see images at all, so without this it would report
        a hand-signed contract as unsigned. Phrased as an observation rather
        than a conclusion: ink near a signature line is strong evidence of a
        signature, but it could also be a stamp or an initial, and the model
        has the surrounding text needed to judge.
        """
        if not self.anchors_examined:
            return ("No signature area was located in this document's layout, so no "
                    "drawing/image check was possible.")
        if self.found:
            return (
                f"IMPORTANT - the text above contains no image or drawing content, but a "
                f"geometric scan of the PDF found {self.detail}. Handwritten and pasted "
                f"signatures appear as drawings or images and are invisible in the "
                f"extracted text. Treat this as strong evidence the document IS signed, "
                f"unless the surrounding text indicates the mark is something else (a "
                f"logo, stamp, or watermark). Mention it in signature_evidence."
            )
        return (
            "A geometric scan of the PDF found no drawing or image content in the "
            "signature area(s), so there is no hand-drawn or pasted signature that "
            "the extracted text would have missed. Judge from the text alone."
        )


class SignatureInkDetector:
    """
    Scans a PDF's geometry for ink near signature labels.

    Anchors on the *word* "Signature"/"Signed" rather than on a drawn
    signature rule. That matters: of the four sample documents, only ACME
    draws real line objects for its signature rules - CloudShield's are
    literal underscore characters and the two purchase orders have no rule
    at all - so a line-based scan would find a signature area in one
    document out of four, while word anchoring finds one in all four.
    """

    def __init__(
        self,
        band_above: int = BAND_ABOVE,
        band_below: int = BAND_BELOW,
        band_right: int = BAND_RIGHT,
    ):
        """
        Args:
            band_above: Points above a signature label to search.
            band_below: Points below it to search.
            band_right: Points to the right of the label's left edge,
                roughly one column's width.
        """
        self.band_above = band_above
        self.band_below = band_below
        self.band_right = band_right

    def detect(self, file_path: str) -> SignatureInkFinding:
        """
        Scan a PDF for non-text ink near its signature area(s).

        Args:
            file_path (str): Path to the PDF.

        Returns:
            SignatureInkFinding: What was found. A scan that fails for any
                reason returns an empty finding rather than raising - this
                is supplementary evidence, and must never break an
                extraction that would otherwise succeed.
        """
        try:
            with pdfplumber.open(file_path) as pdf:
                return self._scan(pdf)
        except Exception:
            return SignatureInkFinding()

    def _scan(self, pdf) -> SignatureInkFinding:
        anchors_examined = 0
        descriptions: List[str] = []

        for page_number, page in enumerate(pdf.pages, start=1):
            anchors = [
                word for word in page.extract_words()
                if SIGNATURE_WORD.search(word["text"])
            ]
            anchors_examined += len(anchors)
            if not anchors:
                continue

            bands = [(anchor, self._band(anchor, anchors)) for anchor in anchors]

            # Attribute each piece of ink to the ONE signature area it sits
            # in most, rather than reporting it against every band it
            # happens to clip. A signature that starts slightly left of its
            # own label would otherwise also be credited to the party in the
            # column to the left.
            for kind, label, bbox in self._ink_objects(page):
                anchor = self._best_match(bbox, bands)
                if anchor is not None:
                    descriptions.append(
                        f"{kind}{label} beside '{anchor['text']}' "
                        f"at x={anchor['x0']:.0f} on page {page_number}"
                    )

        if not descriptions:
            return SignatureInkFinding(found=False, anchors_examined=anchors_examined)

        return SignatureInkFinding(
            found=True,
            detail="; ".join(descriptions),
            anchors_examined=anchors_examined,
        )

    ROW_TOLERANCE = 12  # points; anchors this close vertically are the same row

    def _band(self, anchor, anchors) -> tuple:
        """
        The region around a signature label where its signature would sit,
        as (left, top, right, bottom).

        The right edge stops at the next signature label on the same row.
        Signature blocks routinely put vendor and customer side by side, and
        a fixed-width band reaches from one column into the other.
        """
        right = anchor["x0"] + self.band_right
        neighbours = [
            other["x0"] for other in anchors
            if other is not anchor
            and other["x0"] > anchor["x0"]
            and abs(other["top"] - anchor["top"]) <= self.ROW_TOLERANCE
        ]
        if neighbours:
            right = min(right, min(neighbours) - 1)

        return (
            anchor["x0"],
            anchor["top"] - self.band_above,
            right,
            anchor["bottom"] + self.band_below,
        )

    def _ink_objects(self, page) -> List[tuple]:
        """
        Everything on a page that could be a signature, as
        (kind, label, bbox) triples.

        Lines are excluded deliberately: a signature *rule* is itself a
        line, so counting lines would report every blank signature line as
        signed.
        """
        found = []

        for kind, attribute in (("image", "images"), ("drawing", "curves")):
            for obj in getattr(page, attribute, []) or []:
                found.append(
                    (kind, "", (obj["x0"], obj["top"], obj["x1"], obj["bottom"]))
                )

        for annot in (page.annots or []):
            subtype = self._annotation_subtype(annot)
            if subtype not in SIGNATURE_ANNOTATION_SUBTYPES:
                continue
            # A typed signature carries its text; quoting it gives the model
            # something concrete to weigh instead of a bare "something is
            # there".
            contents = (annot.get("contents") or annot.get("title") or "").strip()
            label = f" ({subtype}" + (f": '{contents}'" if contents else "") + ")"
            found.append((
                "annotation", label,
                (annot["x0"], annot["top"], annot["x1"], annot["bottom"]),
            ))

        return found

    @staticmethod
    def _annotation_subtype(annot) -> str:
        """Normalize an annotation's /Subtype to a plain name like 'Stamp'."""
        data = annot.get("data") or {}
        raw = data.get("Subtype") if hasattr(data, "get") else None
        if raw is None:
            return ""
        return getattr(raw, "name", None) or str(raw).strip("/'\" ")

    @staticmethod
    def _overlap_area(bbox, band) -> float:
        width = min(bbox[2], band[2]) - max(bbox[0], band[0])
        height = min(bbox[3], band[3]) - max(bbox[1], band[1])
        return width * height if width > 0 and height > 0 else 0.0

    def _best_match(self, bbox, bands):
        """
        The signature area a piece of ink belongs to: the one it overlaps
        most, or None if it overlaps none.
        """
        best, best_area = None, 0.0
        for anchor, band in bands:
            area = self._overlap_area(bbox, band)
            if area > best_area:
                best, best_area = anchor, area
        return best
