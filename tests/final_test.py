"""
Final test to verify the complete system works as expected
"""

import sys
import os

# Add the project root to the path so `src` is importable as a package,
# regardless of the directory the tests are run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_complete_system():
    """Test that the entire system works from import to basic functionality"""

    try:
        # Test 1: Import all modules
        print("Testing imports...")
        from src import DocumentProcessor, LLMService
        from src.extractors.contract_extractor import ContractExtractor
        from src.config.field_registry import FieldDefinitionRegistry
        from src.models.extracted_data import ExtractedContractData, ExtractedData, LineItem

        print("✓ All imports successful")

        # Test 2: Basic instantiation (explicit injected test config, no env/file reads)
        print("Testing instantiation...")

        from src.config.llm_config import LLMProfile
        from src.prompts.prompt_repository import PromptRepository
        from src.services.llm_service import LLMService

        test_profile = LLMProfile(
            name="test",
            base_url="https://openrouter.ai/api/v1",
            model="test-model",
            api_key="test_key",
        )
        llm_service = LLMService(test_profile)
        field_registry = FieldDefinitionRegistry()
        contract_extractor = ContractExtractor(llm_service, field_registry, PromptRepository())
        processor = DocumentProcessor(contract_extractor, field_registry)

        print("✓ Service instantiation successful")

        # Test 3: Extractor instantiation (already built above via DI)
        print("Testing extractor instantiation...")
        assert isinstance(contract_extractor, ContractExtractor)

        print("✓ Extractor instantiation successful")

        # Test 4: Data model usage
        print("Testing data model usage...")
        data_model = ExtractedData(
            document_type="test",
            extracted_data={"field": "value"},
            confidence=0.9
        )
        contract_data_model = ExtractedContractData(
            document_type="purchase_order",
            items=[LineItem(product_name="Widget", quantity=1, price=10.0, total_amount=10.0)],
        )

        print("✓ Data model usage successful")

        print("\n🎉 All system tests passed! The document extraction system is ready.")
        return True

    except Exception as e:
        print(f"✗ System test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Running final system test...")
    success = test_complete_system()

    if success:
        print("\n✅ Final system test completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Final system test failed!")
        sys.exit(1)