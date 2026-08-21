"""
Basic test for the document data extraction system
"""

import os
import sys

# Add the project root to the path so `src` is importable as a package,
# regardless of the directory the tests are run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import process_document

def test_imports():
    """Test that all modules can be imported successfully"""
    try:
        from src.services.document_processor import DocumentProcessor
        from src.services.llm_service import LLMService
        from src.extractors.contract_extractor import ContractExtractor
        from src.config.field_registry import FieldDefinitionRegistry
        from src.models.extracted_data import ExtractedContractData, ExtractedData, LineItem

        print("✓ All modules imported successfully")
        return True
    except Exception as e:
        print(f"✗ Import test failed: {e}")
        return False

def test_basic_functionality():
    """Test basic functionality of the system"""
    try:
        # Test that we can create instances of core classes
        from src.config.field_registry import FieldDefinitionRegistry
        from src.config.llm_config import LLMProfile
        from src.extractors.contract_extractor import ContractExtractor
        from src.prompts.prompt_repository import PromptRepository
        from src.services.document_processor import DocumentProcessor
        from src.services.llm_service import LLMService

        print("✓ Service classes can be instantiated")

        # LLMService takes its config via constructor injection, so no
        # environment variables or files are needed here.
        test_profile = LLMProfile(
            name="test",
            base_url="https://openrouter.ai/api/v1",
            model="test-model",
            api_key="test_key",
        )
        llm_service = LLMService(test_profile)
        field_registry = FieldDefinitionRegistry()
        contract_extractor = ContractExtractor(llm_service, field_registry, PromptRepository())
        document_processor = DocumentProcessor(contract_extractor, field_registry)
        print("✓ LLM service and document processor can be created")

        return True
    except Exception as e:
        print(f"✗ Basic functionality test failed: {e}")
        return False

if __name__ == "__main__":
    print("Running basic tests for document data extraction system...")

    success = True
    success &= test_imports()
    success &= test_basic_functionality()

    if success:
        print("\n✓ All basic tests passed!")
        sys.exit(0)
    else:
        print("\n✗ Some tests failed!")
        sys.exit(1)