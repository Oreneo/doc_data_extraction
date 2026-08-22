"""
Read model for contract data loaded back out of the database.
"""

from typing import List, Optional

from pydantic import BaseModel

from .extracted_data import ExtractedContractData


class StoredContract(BaseModel):
    """
    One row of processed_documents together with the contract data stored
    beneath it, as returned by ContractRepository.fetch_all().

    Pairs the audit metadata (which file, when, whether it succeeded) with
    the extracted fields, which live in separate header/items tables and
    are absent for documents that failed extraction or whose type has no
    table pair.
    """

    source_file: str
    document_type: str
    processed_at: str
    status: str                                     # extracted | failed | skipped_type
    error: Optional[str] = None
    warnings: List[str] = []
    data: Optional[ExtractedContractData] = None
