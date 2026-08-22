"""
Document Processor for handling different document types and extraction workflows
"""

import glob
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List

import pdfplumber

from ..config.field_registry import FieldDefinitionRegistry
from ..extractors.contract_extractor import ContractExtractor
from ..reporting.progress_reporter import NullProgressReporter, Timer
from ..storage.contract_repository import (
    AbstractContractRepository,
    NullContractRepository,
)
from ..utils.signature_ink import SignatureInkDetector
from .quality_checker import ExtractionQualityChecker

# How much of the document's opening text to scan for a type-identifying
# keyword (title/heading), rather than the whole body - see
# _identify_document_type.
DOCUMENT_TYPE_SCAN_CHARS = 1000


class DocumentProcessor:
    """
    Main processor: extracts PDF text, identifies document type and
    customer, and delegates field extraction to the injected ContractExtractor.
    """

    def __init__(
        self,
        contract_extractor: ContractExtractor,
        field_registry: FieldDefinitionRegistry,
        repository: AbstractContractRepository = None,
        progress=None,
        signature_detector: SignatureInkDetector = None,
        quality_checker: ExtractionQualityChecker = None,
    ):
        """
        Args:
            contract_extractor: Extractor for the canonical contract field schema.
            field_registry: Used here for its customer keyword matching.
            repository: Where extraction results are persisted. Defaults to a
                no-op repository, so the pipeline runs without a database.
            progress: Receives live progress events. Defaults to a silent
                reporter, so nothing prints unless a caller asks for it.
            signature_detector: Finds drawn/scanned signatures that text
                extraction cannot see.
            quality_checker: Sanity-checks each result against the document
                text, catching fields the model silently dropped.
        """
        self.contract_extractor = contract_extractor
        self.field_registry = field_registry
        self.repository = repository or NullContractRepository()
        self.progress = progress or NullProgressReporter()
        self.signature_detector = signature_detector or SignatureInkDetector()
        self.quality_checker = quality_checker or ExtractionQualityChecker()
        self.supported_extensions = ['.pdf']

    def process_file(self, file_path: str) -> Dict[str, Any]:
        """
        Process a document file, extract data, and persist the result.

        Every run extracts every document from scratch. Stored results are
        output, never an input: nothing about a previous run changes what
        this one does, so the same documents always produce the same work
        and a result is always reproducible from the documents alone.

        Args:
            file_path (str): Path to the document file

        Returns:
            Dict[str, Any]: Extracted data from the document, including the
                canonical `source_file` it was stored under.
        """
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in self.supported_extensions:
            raise ValueError(f"Unsupported file type: {ext}")

        source_file = self.canonical_path(file_path)

        text_content, page_count = self._extract_text_from_pdf(source_file)
        self.progress.text_extracted(page_count, len(text_content))

        # Text extraction is blind to images, drawings and annotations, so
        # scan the PDF geometry for a signature the text cannot show.
        signature_finding = self.signature_detector.detect(source_file)

        content_hash = self._content_hash(text_content, signature_finding)

        document_type = self._identify_document_type(text_content)
        customer_key = self.field_registry.identify_customer(text_content)
        self.progress.identified(document_type, customer_key)

        self.progress.signature_scanned(
            signature_finding.found, signature_finding.anchors_examined
        )

        signature_hint = signature_finding.as_prompt_hint()

        self.progress.llm_call_started()
        with Timer() as timer:
            result = self.contract_extractor.extract(
                text_content, document_type, customer_key,
                signature_hint=signature_hint,
            )
        self.progress.llm_call_finished(timer.seconds)

        result = self._check_and_maybe_retry(
            text_content, result, document_type, customer_key, signature_hint
        )

        self.repository.save(result, source_file, content_hash)
        self.progress.document_stored(len(result.items), result.error)

        stored = result.model_dump()
        stored["source_file"] = source_file
        return stored

    def _check_and_maybe_retry(
        self, text_content, result, document_type, customer_key, signature_hint
    ):
        """
        Run the quality checks, and give the model one more attempt if they
        found a dropped field.

        Bounded to a single retry: two attempts, then the result is accepted
        with its warning. The warning is kept either way - a retry that
        succeeds still records that the first attempt failed, because
        quietly repairing it would hide the accuracy signal this exists to
        surface.

        Returns:
            ExtractedContractData: the better of the two attempts.
        """
        warnings = self.quality_checker.check(text_content, result)
        if not warnings:
            return result

        self.progress.quality_warning(warnings, retrying=True)

        retry_note = self.quality_checker.retry_note(warnings)
        self.progress.llm_call_started(retry=True)
        with Timer() as timer:
            retried = self.contract_extractor.extract(
                text_content, document_type, customer_key,
                signature_hint=signature_hint,
                retry_note=retry_note,
            )
        self.progress.llm_call_finished(timer.seconds)

        retry_warnings = self.quality_checker.check(text_content, retried)

        if retry_warnings or retried.error is not None:
            # The retry did no better - keep the first result rather than
            # assuming a second attempt is automatically an improvement.
            self.progress.quality_warning(warnings, retrying=False)
            result.warnings = warnings
            return result

        retried.warnings = [f"{w} (resolved on retry)" for w in warnings]
        return retried

    @staticmethod
    def canonical_path(file_path: str) -> str:
        """
        Resolve a path to the single canonical form a document is stored
        under.

        A document's identity in the database is its path, and the UNIQUE
        constraint on it compares raw strings - so "sample_docs/x.pdf" and
        "/abs/.../sample_docs/x.pdf" would be two documents, duplicating the
        row for one document. Resolving at the boundary
        (absolute, "." and ".." collapsed, symlinks followed) means every
        spelling of one file converges here and nothing downstream has to
        know this problem exists.

        Args:
            file_path (str): Path as supplied by the caller.

        Returns:
            str: Absolute, resolved path.
        """
        return str(Path(file_path).resolve())

    @staticmethod
    def _content_hash(text_content: str, signature_finding=None) -> str:
        """
        Fingerprint what a document contained at the moment it was
        extracted, stored as audit data.

        This is recorded, never consulted: every run re-extracts every
        document, so nothing here decides whether work happens. It answers
        "was this the same document last time?" after the fact.

        Hashing the extracted *text* rather than the PDF bytes means a file
        merely re-saved or re-compressed fingerprints the same. Text alone
        is not enough though - signatures are usually PDF annotations, which
        never appear in extracted text (the signed and unsigned CloudShield
        copies produce byte-identical text), so the signature scan is folded
        in to keep the fingerprint honest.

        Args:
            text_content (str): Text content of the document
            signature_finding: Result of the signature ink scan, if any.

        Returns:
            str: Hex-encoded SHA-256 digest
        """
        parts = [text_content]
        if signature_finding is not None:
            parts.append(f"signature:{signature_finding.found}:{signature_finding.detail or ''}")

        return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()

    def _extract_text_from_pdf(self, file_path: str) -> str:
        """
        Extract text content from a PDF file

        Args:
            file_path (str): Path to the PDF file

        Returns:
            tuple: (extracted text content, page count)
        """
        text_content = ""
        page_count = 0

        try:
            with pdfplumber.open(file_path) as pdf:
                page_count = len(pdf.pages)
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_content += page_text + "\n"
        except Exception as e:
            raise RuntimeError(f"Failed to extract text from PDF: {e}")

        return text_content, page_count

    def _identify_document_type(self, text_content: str) -> str:
        """
        Identify the type of document based on content

        Only the opening of the document is scanned (its title/heading),
        not the full body: an Order Form can legitimately mention "Purchase
        Order" deep in a boilerplate section (e.g. "Is a Purchase Order (PO)
        required for this Order Form? No") without being one, so scanning
        the whole text risks a false match on whichever keyword happens to
        appear first in the body rather than the document's actual type.

        Args:
            text_content (str): Text content of the document

        Returns:
            str: Document type identifier
        """
        lower_text = text_content[:DOCUMENT_TYPE_SCAN_CHARS].lower()

        if "purchase order" in lower_text or "po #" in lower_text:
            return "purchase_order"
        elif "order form" in lower_text or "order from" in lower_text:
            return "order_form"
        elif "invoice" in lower_text:
            return "invoice"
        else:
            return "generic"

    def process_multiple_documents(self, file_paths: List[str]) -> Dict[str, Any]:
        """
        Process multiple documents and return aggregated results

        Args:
            file_paths (List[str]): List of document file paths

        Returns:
            Dict[str, Any]: Aggregated extraction results, keyed by the
                canonical path each document was stored under - so callers
                can scope a report to exactly what this run touched.
        """
        results = {}
        total = len(file_paths)

        for index, file_path in enumerate(file_paths, start=1):
            # Key by the canonical path even when processing fails, so the
            # keys are consistent with what the repository stored.
            key = self.canonical_path(file_path)
            self.progress.document_started(index, total, key)
            try:
                results[key] = self.process_file(file_path)
            except Exception as e:
                self.progress.document_stored(0, str(e))
                results[key] = {"error": str(e)}

        self.progress.run_finished()
        return results

    def process_folder(self, folder_path: str) -> Dict[str, Any]:
        """
        Process every supported document in a folder - the assignment's
        "documents landing in a dedicated folder" entry point.

        Re-running over the same folder re-extracts every document: stored
        results are output, never a cache that suppresses work.

        Args:
            folder_path (str): Path to the folder to process

        Returns:
            Dict[str, Any]: Aggregated extraction results, keyed by file path
        """
        return self.process_multiple_documents(self.list_documents(folder_path))

    def list_documents(self, folder_path: str) -> List[str]:
        """
        List the supported documents in a folder, in a stable order.

        Separate from process_folder so a caller can know how many documents
        a run covers before it starts - the progress output announces the
        total up front.

        Args:
            folder_path (str): Path to the folder to scan

        Returns:
            List[str]: Sorted paths of supported documents
        """
        if not os.path.isdir(folder_path):
            raise ValueError(f"Not a folder: {folder_path}")

        return sorted(
            path
            for ext in self.supported_extensions
            for path in glob.glob(os.path.join(folder_path, f"*{ext}"))
        )
