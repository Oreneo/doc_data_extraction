"""
Simple test to verify imports work correctly
"""

import os
import sys

# Add the project root to the path so `src` is importable as a package,
# regardless of the directory the tests are run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_imports():
    """Test that all modules can be imported successfully"""
    try:
        # Test basic imports
        import src
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

if __name__ == "__main__":
    print("Testing imports for document data extraction system...")

    success = test_imports()

    if success:
        print("\n✓ All import tests passed!")
    else:
        print("\n✗ Some import tests failed!")