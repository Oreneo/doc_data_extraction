"""
Document Processor for handling different document types and extraction workflows
"""

import glob
import hashlib
import os
from typing import Any, Dict, List

import pdfplumber

from ..config.field_registry import FieldDefinitionRegistry
from ..extractors.contract_extractor import ContractExtractor
from ..storage.contract_repository import (
    AbstractContractRepository,
    NullContractRepository,
)

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
    ):
        """
        Args:
            contract_extractor: Extractor for the canonical contract field schema.
            field_registry: Used here for its customer keyword matching.
            repository: Where extraction results are persisted. Defaults to a
                no-op repository, so the pipeline runs without a database.
        """
        self.contract_extractor = contract_extractor
        self.field_registry = field_registry
        self.repository = repository or NullContractRepository()
        self.supported_extensions = ['.pdf']

    def process_file(self, file_path: str) -> Dict[str, Any]:
        """
        Process a document file, extract data, and persist the result.

        A document whose text is unchanged since the last run is skipped
        before the LLM is called - re-running a folder after adding one file
        then costs one extraction rather than one per document.

        Args:
            file_path (str): Path to the document file

        Returns:
            Dict[str, Any]: Extracted data from the document
        """
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in self.supported_extensions:
            raise ValueError(f"Unsupported file type: {ext}")

        text_content = self._extract_text_from_pdf(file_path)
        content_hash = self._hash_text(text_content)

        if self.repository.is_unchanged(file_path, content_hash):
            return {"source_file": file_path, "skipped": True, "reason": "unchanged"}

        document_type = self._identify_document_type(text_content)
        customer_key = self.field_registry.identify_customer(text_content)

        result = self.contract_extractor.extract(text_content, document_type, customer_key)
        self.repository.save(result, file_path, content_hash)

        return result.model_dump()

    @staticmethod
    def _hash_text(text_content: str) -> str:
        """
        Hash a document's extracted text, to detect whether it has changed
        since the last run.

        Hashing the extracted *text* rather than the PDF bytes means a file
        that was merely re-saved or re-compressed, without its content
        changing, is still recognized as unchanged.

        Args:
            text_content (str): Text content of the document

        Returns:
            str: Hex-encoded SHA-256 digest
        """
        return hashlib.sha256(text_content.encode("utf-8")).hexdigest()

    def _extract_text_from_pdf(self, file_path: str) -> str:
        """
        Extract text content from a PDF file

        Args:
            file_path (str): Path to the PDF file

        Returns:
            str: Extracted text content
        """
        text_content = ""

        try:
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_content += page_text + "\n"
        except Exception as e:
            raise RuntimeError(f"Failed to extract text from PDF: {e}")

        return text_content

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
            Dict[str, Any]: Aggregated extraction results
        """
        results = {}

        for file_path in file_paths:
            try:
                results[file_path] = self.process_file(file_path)
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
                results[file_path] = {"error": str(e)}

        return results

    def process_folder(self, folder_path: str) -> Dict[str, Any]:
        """
        Process every supported document in a folder - the assignment's
        "documents landing in a dedicated folder" entry point.

        Re-running over the same folder is safe and cheap: unchanged
        documents are skipped by process_file before any LLM call.

        Args:
            folder_path (str): Path to the folder to process

        Returns:
            Dict[str, Any]: Aggregated extraction results, keyed by file path
        """
        if not os.path.isdir(folder_path):
            raise ValueError(f"Not a folder: {folder_path}")

        file_paths = sorted(
            path
            for ext in self.supported_extensions
            for path in glob.glob(os.path.join(folder_path, f"*{ext}"))
        )

        return self.process_multiple_documents(file_paths)
