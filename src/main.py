"""
Main entry point for the document data extraction system
"""

import os
import sys
from typing import Any, Dict, Optional

# Add the project root to the path so `src` is importable as a package.
# (Importing everything through the `src` package - rather than adding
# src/ itself to the path and importing its contents as flat top-level
# modules - keeps a single identity per module and lets modules use
# relative imports (e.g. `..config`) to reach sibling packages.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.field_registry import FieldDefinitionRegistry
from src.config.llm_config import load_llm_config
from src.config.pipeline_config import load_pipeline_config
from src.config.storage_config import load_storage_config
from src.extractors.contract_extractor import ContractExtractor
from src.prompts.prompt_repository import PromptRepository
from src.reporting.console_reporter import ConsoleReporter
from src.reporting.progress_reporter import ConsoleProgressReporter
from src.services.document_processor import DocumentProcessor
from src.services.llm_service import LLMService
from src.storage.contract_repository import SqliteContractRepository
from src.storage.database import Database


def _build_pipeline():
    """
    Composition root: resolves the active LLM profile, prompt templates,
    field schema, and database, and wires them into the services that need
    them. This is the only place that reads configuration or constructs
    dependencies.

    Returns:
        tuple: (DocumentProcessor, SqliteContractRepository, StorageConfig,
            LLMProfile). The repository is handed back because the report
            reads from it after processing finishes; the LLM profile so the
            run can announce which model it's using.
    """
    llm_config = load_llm_config()
    storage_config = load_storage_config()
    prompt_repository = PromptRepository()
    field_registry = FieldDefinitionRegistry()

    llm_service = LLMService(llm_config)
    contract_extractor = ContractExtractor(llm_service, field_registry, prompt_repository)

    database = Database(storage_config.database_path)
    repository = SqliteContractRepository(database)

    processor = DocumentProcessor(
        contract_extractor, field_registry, repository, ConsoleProgressReporter()
    )
    return processor, repository, storage_config, llm_config


def _build_document_processor() -> DocumentProcessor:
    """
    Build just the document processor, for the library API below.
    """
    processor, _, _, _ = _build_pipeline()
    return processor


def run(target: Optional[str] = None) -> int:
    """
    Run the full pipeline: process documents, persist them, then print a
    report of everything in the database.

    The report is read back out of SQLite rather than from the results held
    in memory, so it reflects what was actually stored. Reading is the only
    thing the database is used for here: every run extracts every document
    from scratch, so stored results never decide what work happens.

    Args:
        target: Optional single document or folder to process instead of
            the configured input folder. With no argument, the folder from
            config/pipeline.yaml (or INPUT_FOLDER) is processed.

    Returns:
        int: Process exit code - 0 on success, 1 if the run failed.
    """
    try:
        processor, repository, storage_config, llm_config = _build_pipeline()

        if target is None:
            target = load_pipeline_config().input_folder

        if os.path.isdir(target):
            file_count = len(processor.list_documents(target))
            processor.progress.run_started(
                target, file_count, llm_config.name, llm_config.model
            )
            results = processor.process_folder(target)
            touched = set(results)
        else:
            processor.progress.run_started(
                target, 1, llm_config.name, llm_config.model
            )
            processor.progress.document_started(1, 1, target)
            result = processor.process_file(target)
            processor.progress.run_finished()
            touched = {result["source_file"]}

        # Scope the report to what this run actually touched. Everything else
        # in the database is still accounted for, as a "not shown" line.
        ConsoleReporter().report(
            repository.fetch_all(), storage_config.database_path, only_paths=touched
        )
        return 0

    except Exception as e:
        print(f"Pipeline failed: {e}", file=sys.stderr)
        return 1


def process_document(file_path: str) -> Dict[str, Any]:
    """
    Process a document file and extract data from it

    Args:
        file_path (str): Path to the document file to process

    Returns:
        Dict[str, Any]: Extracted data from the document
    """
    try:
        document_processor = _build_document_processor()

        # Process the document
        result = document_processor.process_file(file_path)

        return result

    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }

def process_multiple_documents(file_paths: list) -> Dict[str, Any]:
    """
    Process multiple document files and extract data from them

    Args:
        file_paths (list): List of document file paths to process

    Returns:
        Dict[str, Any]: Extracted data from all documents
    """
    try:
        document_processor = _build_document_processor()

        # Process the documents
        result = document_processor.process_multiple_documents(file_paths)

        return result

    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }

def process_folder(folder_path: str) -> Dict[str, Any]:
    """
    Process every supported document in a folder and extract data from them

    Args:
        folder_path (str): Path to the folder to process

    Returns:
        Dict[str, Any]: Extracted data from all documents in the folder
    """
    try:
        document_processor = _build_document_processor()

        return document_processor.process_folder(folder_path)

    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }

if __name__ == "__main__":
    # No arguments: process the configured input folder and report.
    # An optional path may be passed to process a single document or a
    # different folder instead - handy when chasing one document without
    # re-running the whole set.
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))