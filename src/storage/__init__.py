"""
Storage package: local SQLite persistence for extracted contract data.
"""

from .contract_repository import (
    AbstractContractRepository,
    NullContractRepository,
    SqliteContractRepository,
)
from .database import Database

__all__ = [
    "AbstractContractRepository",
    "Database",
    "NullContractRepository",
    "SqliteContractRepository",
]
