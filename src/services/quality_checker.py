"""
Post-extraction sanity checks.

Catches fields the model silently dropped. The failure this exists for looked
entirely healthy from the outside: a CloudShield document extracted with
status "extracted", totals that reconciled, and a report showing no burst
terms - identical to how a document with no burst clause renders. Two other
copies of the same text found three burst thresholds; that one found none.

Every check compares the document's own text against the result just
extracted from it. Nothing here consults stored data: a run's behaviour must
depend only on the documents it was given.
"""

import os
import re
from typing import List

from ..models.extracted_data import ExtractedContractData

# Mentions of a burst allowance anywhere in the document. Broad on purpose -
# this only decides whether to *expect* a burst term, and a false expectation
# costs one warning while a missed one costs a contractual field.
BURST_MENTION = re.compile(r"\bburst\b", re.IGNORECASE)

BURST_DROPPED = (
    "document text mentions burst terms but none were extracted - "
    "the model may have skipped them"
)


class ExtractionQualityChecker:
    """
    Runs checks that need both the source text and the extracted result.

    Deliberately a class rather than a function: this is injected into
    DocumentProcessor like every other collaborator, and it is the natural
    home for the checks that will follow (payment terms present in the text
    but not extracted, a signature block with no signature_evidence).
    """

    def check(self, text: str, result: ExtractedContractData) -> List[str]:
        """
        Args:
            text: The document's extracted text.
            result: What the extraction produced from that text.

        Returns:
            List[str]: Human-readable warnings, empty when nothing looks wrong.
        """
        if result.error is not None:
            # A failed extraction already reports its error; adding "fields
            # are missing" on top is noise.
            return []

        warnings = []
        if self._burst_dropped(text, result):
            warnings.append(BURST_DROPPED)
        return warnings

    @staticmethod
    def _burst_dropped(text: str, result: ExtractedContractData) -> bool:
        """
        True when the document talks about burst but nothing carries it.

        Keys on what the document says, not on absence alone - a document
        with no burst clause must never warn, however it extracts.
        """
        if not BURST_MENTION.search(text or ""):
            return False
        return not any(item.burst for item in result.items)

    @staticmethod
    def check_filename(document_type, filename_type, source_file) -> List[str]:
        """
        Flag a filename that disagrees with the document's own content.

        The content decides the type; this never overrides it. But a
        mismatch is worth surfacing - it usually means a misfiled or
        misnamed document, and it is the kind of thing nobody notices until
        they go looking for a contract under the wrong name.

        Args:
            document_type: The type decided from the document's content.
            filename_type: The type the filename suggests, or None.
            source_file: Path, for naming the file in the warning.

        Returns:
            List[str]: One warning if they disagree, else empty.
        """
        if not filename_type or filename_type == document_type:
            return []

        name = os.path.basename(source_file) if source_file else "the filename"
        return [
            f"filename suggests '{filename_type}' but the document's content "
            f"reads as '{document_type}' - content was used ({name})"
        ]

    @staticmethod
    def retry_note(warnings: List[str]) -> str:
        """
        A corrective instruction for a second extraction attempt.

        This has to exist because of how the first fix interacts with the
        third: at temperature 0 the model is close to deterministic, so
        re-sending an identical prompt buys an identical wrong answer at
        full price. The retry must differ, and pointing at the specific
        field that went missing beats vaguely asking for more care.

        Args:
            warnings: What the check found.

        Returns:
            str: Text to append to the prompt, or "" if nothing to correct.
        """
        if BURST_DROPPED not in warnings:
            return ""

        return (
            "\n\nIMPORTANT - CORRECTION: a first attempt at this document returned no "
            "burst terms, but the document text does mention them. Re-read the document "
            "carefully, find every burst clause, and attach it to the line items it "
            "covers, following the burst rules above. Do not return null for burst on "
            "every item."
        )
