"""
Document Data Extraction System
"""

__version__ = "0.1.0"
__author__ = "Document Extraction Team"

# Import main components for easy access
from .services.document_processor import DocumentProcessor
from .services.llm_service import LLMService

__all__ = ['DocumentProcessor', 'LLMService']