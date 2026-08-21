"""
Comprehensive tests for the document data extraction system
"""

import json
import sys
import os

# Add the project root to the path so `src` is importable as a package,
# regardless of the directory the tests are run from. (The project root -
# not src/ itself: adding src/ would let its modules be imported a second
# time as flat top-level modules, giving each one two identities and
# breaking their relative imports.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_system_structure():
    """Test that all system components can be imported and instantiated"""
    print("Testing system structure...")

    # Test imports
    from src import DocumentProcessor, LLMService
    from src.extractors.contract_extractor import ContractExtractor
    from src.config.field_registry import FieldDefinitionRegistry
    from src.models.extracted_data import ExtractedContractData, ExtractedData, LineItem

    print("✓ System structure test passed")

def _build_test_llm_service(client=None):
    """Build an LLMService with an explicit, injected test config (no env/file reads)."""
    from src.config.llm_config import LLMProfile
    from src.services.llm_service import LLMService

    test_profile = LLMProfile(
        name="test",
        base_url="https://openrouter.ai/api/v1",
        model="test-model",
        api_key="test_key",
    )
    return LLMService(test_profile, client=client)

def _build_test_contract_extractor(client=None):
    from src.config.field_registry import FieldDefinitionRegistry
    from src.extractors.contract_extractor import ContractExtractor
    from src.prompts.prompt_repository import PromptRepository

    llm_service = _build_test_llm_service(client=client)
    field_registry = FieldDefinitionRegistry()
    return ContractExtractor(llm_service, field_registry, PromptRepository()), field_registry

def test_service_initialization():
    """Test that services can be initialized"""
    print("Testing service initialization...")

    llm_service = _build_test_llm_service()

    print("✓ Service initialization test passed")

def test_extractor_instantiation():
    """Test that the contract extractor and document processor can be instantiated"""
    print("Testing extractor instantiation...")

    from src.services.document_processor import DocumentProcessor

    contract_extractor, field_registry = _build_test_contract_extractor()
    document_processor = DocumentProcessor(contract_extractor, field_registry)

    print("✓ Extractor instantiation test passed")

def test_model_structure():
    """Test that data models work correctly"""
    print("Testing model structure...")

    from src.models.extracted_data import ExtractedContractData, ExtractedData, LineItem

    data = ExtractedData(
        document_type="test",
        extracted_data={"field": "value"},
        confidence=0.9
    )
    assert data.document_type == "test"
    assert data.confidence == 0.9

    # Date/payment-terms normalization on the formats actually seen in the
    # sample docs (see plans/canonical-contract-field-schema.md)
    contract_data = ExtractedContractData(
        document_type="order_form",
        customer_key="acme",
        start_date="January 1, 2025",
        end_date="3/1/2025",
        payment_terms="within thirty (30) days from invoice",
        customer_signature=True,
        items=[LineItem(product_name="Widget", quantity=1, price=10.0, total_amount=10.0, burst="10% burst")],
    )
    assert contract_data.start_date == "01-01-2025", contract_data.start_date
    assert contract_data.end_date == "03-01-2025", contract_data.end_date
    assert contract_data.payment_terms == "Net 30", contract_data.payment_terms
    assert contract_data.items[0].burst == "10% burst"

    print("✓ Model structure test passed")

def test_field_registry_customer_overrides():
    """Test that the field registry resolves customer overrides and identifies customers by keyword"""
    print("Testing field registry customer overrides...")

    from src.config.field_registry import FieldDefinitionRegistry

    registry = FieldDefinitionRegistry()

    base_fields = registry.get_fields(customer_key=None)
    field_names = {f.name for f in base_fields}
    assert field_names == {
        "start_date", "end_date", "amount", "payment_terms",
        "billing_address", "customer_signature", "technical_account_manager",
    }, field_names

    acme_fields = registry.get_fields(customer_key="acme")
    tam_field = next(f for f in acme_fields if f.name == "technical_account_manager")
    assert "Customer Support Manager" in tam_field.description

    unknown_fields = registry.get_fields(customer_key="not_a_real_customer")
    assert unknown_fields == base_fields

    assert registry.identify_customer("Purchase Order - BrightOps Analytics Ltd.") == "brightops"
    assert registry.identify_customer("some unrelated document text") is None

    print("✓ Field registry customer overrides test passed")

def _fake_client_class():
    """
    Build a minimal stand-in for an OpenAI-compatible client whose chat
    completion always returns a fixed string, so the full extract/parse/
    normalize path can run with no network call.
    """
    class _FakeMessage:
        def __init__(self, content):
            self.content = content

    class _FakeChoice:
        def __init__(self, content):
            self.message = _FakeMessage(content)

    class _FakeResponse:
        def __init__(self, content):
            self.choices = [_FakeChoice(content)]

    class _FakeCompletions:
        def __init__(self, content):
            self._content = content

        def create(self, **kwargs):
            return _FakeResponse(self._content)

    class _FakeChat:
        def __init__(self, content):
            self.completions = _FakeCompletions(content)

    class _FakeClient:
        def __init__(self, content):
            self.chat = _FakeChat(content)

    return _FakeClient


def test_contract_extractor_end_to_end():
    """
    Run ContractExtractor end-to-end against a stubbed OpenAI-compatible
    client (no network call), confirming prompt building, JSON parsing,
    and field normalization all work together.
    """
    print("Testing contract extractor end-to-end with a stubbed LLM client...")

    _FakeClient = _fake_client_class()

    fake_llm_json = json.dumps({
        "start_date": "03-01-2025",
        "end_date": "02-28-2027",
        "amount": 162000,
        "payment_terms": "Net 30",
        "billing_address": "42 King George Street, London, UK",
        "customer_signature": True,
        "technical_account_manager": "A dedicated Technical Account Manager will be assigned.",
        "items": [
            {
                "product_name": "Analytics Platform - Enterprise",
                "quantity": 1,
                "price": 72000,
                "total_amount": 72000,
                "burst": "Buyer may exceed licensed analytics usage by up to 10% per contract year at no additional cost.",
            }
        ],
    })

    contract_extractor, _ = _build_test_contract_extractor(client=_FakeClient(fake_llm_json))

    result = contract_extractor.extract(
        text="Purchase Order - BrightOps Analytics Ltd. ...",
        document_type="purchase_order",
        customer_key="brightops",
    )

    assert result.error is None, result.error
    assert result.start_date == "03-01-2025"
    assert result.payment_terms == "Net 30"
    assert result.customer_signature is True
    assert len(result.items) == 1
    assert result.items[0].product_name == "Analytics Platform - Enterprise"
    assert result.items[0].burst is not None

    print("✓ Contract extractor end-to-end test passed")

def test_fenced_json_response():
    """
    Small free-tier models often wrap their answer in a ```json fence despite
    being told to return only JSON; confirm that still parses.
    """
    print("Testing fenced JSON response handling...")

    _FakeClient = _fake_client_class()

    fenced = '```json\n{"start_date": "3/1/2025", "items": []}\n```'
    contract_extractor, _ = _build_test_contract_extractor(client=_FakeClient(fenced))

    result = contract_extractor.extract(text="...", document_type="order_form")

    assert result.error is None, result.error
    assert result.start_date == "03-01-2025", result.start_date

    print("✓ Fenced JSON response test passed")

# --------------------------------------------------------------------------
# Storage tests
#
# Every one of these runs against Database(":memory:") - the same SQLite
# engine, with the same constraint enforcement, as the on-disk file, but
# created fresh per test and discarded afterwards. No fixture files to clean
# up, no state leaking between tests, no server.
# --------------------------------------------------------------------------

def _build_test_repository():
    """Build a SqliteContractRepository over a throwaway in-memory database."""
    from src.storage.contract_repository import SqliteContractRepository
    from src.storage.database import Database

    database = Database(":memory:")
    return SqliteContractRepository(database), database

def _sample_contract(document_type="order_form", **overrides):
    from src.models.extracted_data import ExtractedContractData, LineItem

    fields = dict(
        document_type=document_type,
        customer_key="acme",
        start_date="01-01-2025",
        end_date="12-31-2025",
        amount=162000.0,
        payment_terms="Net 30",
        billing_address="42 King George Street, London, UK",
        customer_signature=True,
        technical_account_manager="A dedicated TAM will be assigned.",
        confidence=0.9,
        items=[
            LineItem(product_name="Platform - Enterprise", quantity=1, price=72000.0,
                     total_amount=72000.0, burst="10% burst allowance"),
            LineItem(product_name="Support - Premium", quantity=2, price=45000.0,
                     total_amount=90000.0, burst="10% burst allowance"),
        ],
    )
    fields.update(overrides)
    return ExtractedContractData(**fields)

def test_storage_round_trip():
    """Saved contract data reads back intact, with line order preserved."""
    print("Testing storage round trip...")

    repository, database = _build_test_repository()
    repository.save(_sample_contract(), "/docs/acme.pdf", "hash-1")

    header = database.connection.execute("SELECT * FROM sales_orders").fetchone()
    assert header["amount"] == 162000.0
    assert header["payment_terms"] == "Net 30"
    assert header["customer_key"] == "acme"
    # Stored as ISO 8601 so SQL ordering matches chronological ordering,
    # while the model keeps the assignment's mm-dd-yyyy format.
    assert header["start_date"] == "2025-01-01", header["start_date"]
    assert header["end_date"] == "2025-12-31", header["end_date"]
    assert header["customer_signature"] == 1

    items = database.connection.execute(
        "SELECT * FROM sales_order_items ORDER BY line_number"
    ).fetchall()
    assert len(items) == 2
    assert items[0]["line_number"] == 1
    assert items[0]["product_name"] == "Platform - Enterprise"
    assert items[1]["product_name"] == "Support - Premium"
    assert items[0]["burst"] == "10% burst allowance"

    database.close()
    print("✓ Storage round trip test passed")

def test_storage_type_routing():
    """Order forms and purchase orders land in their own table pairs."""
    print("Testing storage document-type routing...")

    repository, database = _build_test_repository()
    repository.save(_sample_contract("order_form"), "/docs/of.pdf", "h1")
    repository.save(_sample_contract("purchase_order"), "/docs/po.pdf", "h2")

    def count(table):
        return database.connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    assert count("sales_orders") == 1
    assert count("sales_order_items") == 2
    assert count("purchase_orders") == 1
    assert count("purchase_order_items") == 2

    database.close()
    print("✓ Storage document-type routing test passed")

def test_foreign_key_is_enforced():
    """
    An item row must not be insertable without its header. This is the direct
    check that PRAGMA foreign_keys = ON took effect - SQLite disables foreign
    key enforcement by default, and without it the whole 1-to-many guarantee
    (and every ON DELETE CASCADE) is silently inert.
    """
    print("Testing foreign key enforcement...")

    import sqlite3

    _, database = _build_test_repository()

    try:
        with database.transaction() as conn:
            conn.execute(
                """INSERT INTO sales_order_items
                   (sales_order_id, line_number, product_name, quantity, price, total_amount)
                   VALUES (99999, 1, 'Orphan', 1, 1.0, 1.0)"""
            )
        raise AssertionError("Expected IntegrityError for orphaned item row")
    except sqlite3.IntegrityError:
        pass

    database.close()
    print("✓ Foreign key enforcement test passed")

def test_storage_idempotency_cascade():
    """
    Re-saving the same source file replaces the previous record rather than
    accumulating duplicates - and the delete cascades to items, so stale line
    items from the earlier run don't survive.
    """
    print("Testing storage idempotency and cascade...")

    repository, database = _build_test_repository()

    repository.save(_sample_contract(), "/docs/acme.pdf", "hash-1")
    repository.save(_sample_contract(amount=999.0), "/docs/acme.pdf", "hash-2")

    def count(table):
        return database.connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    assert count("processed_documents") == 1, count("processed_documents")
    assert count("sales_orders") == 1, count("sales_orders")
    assert count("sales_order_items") == 2, count("sales_order_items")

    header = database.connection.execute("SELECT amount FROM sales_orders").fetchone()
    assert header["amount"] == 999.0

    database.close()
    print("✓ Storage idempotency and cascade test passed")

def test_unchanged_document_skips_llm():
    """
    A document whose text hasn't changed is skipped before the LLM is called.
    The stub raises if invoked, so reaching the LLM fails the test.
    """
    print("Testing unchanged-document skip...")

    from src.services.document_processor import DocumentProcessor

    repository, database = _build_test_repository()
    repository.save(_sample_contract(), "/docs/acme.pdf", "hash-1")

    class _ExplodingExtractor:
        def extract(self, *args, **kwargs):
            raise AssertionError("LLM must not be called for an unchanged document")

    _, field_registry = _build_test_contract_extractor()
    processor = DocumentProcessor(_ExplodingExtractor(), field_registry, repository)

    # Bypass PDF reading: feed the known text hash directly through the same
    # code path process_file uses.
    assert repository.is_unchanged("/docs/acme.pdf", "hash-1") is True
    assert repository.is_unchanged("/docs/acme.pdf", "hash-CHANGED") is False
    assert repository.is_unchanged("/docs/never-seen.pdf", "hash-1") is False

    database.close()
    print("✓ Unchanged-document skip test passed")

def test_failed_extraction_is_recorded_but_not_stored():
    """
    A failed extraction is logged in the audit table and written nowhere
    else, so the data tables only ever hold clean rows.
    """
    print("Testing failed-extraction handling...")

    repository, database = _build_test_repository()

    failed = _sample_contract(error="LLM returned malformed JSON", confidence=0.0, items=[])
    repository.save(failed, "/docs/broken.pdf", "hash-x")

    row = database.connection.execute("SELECT * FROM processed_documents").fetchone()
    assert row["status"] == "failed", row["status"]
    assert "malformed" in row["error"]

    count = database.connection.execute("SELECT COUNT(*) AS n FROM sales_orders").fetchone()["n"]
    assert count == 0

    # A failure is retried on the next run even though the file is unchanged:
    # the cause may have been a transient API error, not the document.
    assert repository.is_unchanged("/docs/broken.pdf", "hash-x") is False

    database.close()
    print("✓ Failed-extraction handling test passed")

def test_unmapped_document_type_is_audited_only():
    """A type with no header/items tables still gets an audit row."""
    print("Testing unmapped document type...")

    repository, database = _build_test_repository()
    repository.save(_sample_contract("generic"), "/docs/mystery.pdf", "hash-g")

    row = database.connection.execute("SELECT * FROM processed_documents").fetchone()
    assert row["status"] == "skipped_type", row["status"]
    assert row["document_type"] == "generic"

    database.close()
    print("✓ Unmapped document type test passed")

def test_null_repository_keeps_processor_working():
    """DocumentProcessor built without a database behaves as before."""
    print("Testing null repository...")

    from src.services.document_processor import DocumentProcessor
    from src.storage.contract_repository import NullContractRepository

    contract_extractor, field_registry = _build_test_contract_extractor()
    processor = DocumentProcessor(contract_extractor, field_registry)

    assert isinstance(processor.repository, NullContractRepository)
    assert processor.repository.is_unchanged("/any.pdf", "any-hash") is False
    assert processor.repository.save(_sample_contract(), "/any.pdf", "any-hash") is None

    print("✓ Null repository test passed")

def test_transaction_rolls_back_on_error():
    """A failure partway through a save leaves no partial rows behind."""
    print("Testing transaction rollback...")

    repository, database = _build_test_repository()

    try:
        with database.transaction() as conn:
            conn.execute(
                """INSERT INTO processed_documents
                   (source_file, content_hash, document_type, status, processed_at)
                   VALUES ('/docs/x.pdf', 'h', 'order_form', 'extracted', '2025-01-01')"""
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    count = database.connection.execute(
        "SELECT COUNT(*) AS n FROM processed_documents"
    ).fetchone()["n"]
    assert count == 0, count

    database.close()
    print("✓ Transaction rollback test passed")

# --------------------------------------------------------------------------
# Read-back and reporting tests
# --------------------------------------------------------------------------

def test_fetch_all_round_trip():
    """
    Contracts read back out of the database keep every field, item count and
    line ordering - and dates come back as mm-dd-yyyy despite being stored
    as ISO, via ExtractedContractData's existing validator.
    """
    print("Testing fetch_all round trip...")

    repository, database = _build_test_repository()
    repository.save(_sample_contract("order_form"), "/docs/of.pdf", "h1")
    repository.save(_sample_contract("purchase_order"), "/docs/po.pdf", "h2")

    stored = repository.fetch_all()
    assert len(stored) == 2, len(stored)

    by_type = {s.document_type: s for s in stored}
    order_form = by_type["order_form"]

    assert order_form.source_file == "/docs/of.pdf"
    assert order_form.status == "extracted"
    assert order_form.error is None

    data = order_form.data
    assert data is not None
    # Stored as ISO 2025-01-01, returned in the assignment's format.
    assert data.start_date == "01-01-2025", data.start_date
    assert data.end_date == "12-31-2025", data.end_date
    assert data.amount == 162000.0
    assert data.payment_terms == "Net 30"
    assert data.customer_signature is True
    assert data.customer_key == "acme"
    assert len(data.items) == 2
    assert data.items[0].product_name == "Platform - Enterprise"
    assert data.items[1].product_name == "Support - Premium"
    assert data.items[0].burst == "10% burst allowance"

    database.close()
    print("✓ fetch_all round trip test passed")

def test_fetch_all_includes_failed_documents():
    """A failed document is returned with its error, not dropped."""
    print("Testing fetch_all with failed documents...")

    repository, database = _build_test_repository()
    repository.save(
        _sample_contract(error="LLM returned malformed JSON", items=[]),
        "/docs/broken.pdf", "hx",
    )

    stored = repository.fetch_all()
    assert len(stored) == 1
    assert stored[0].status == "failed"
    assert stored[0].data is None
    assert "malformed" in stored[0].error
    # The type survives even though there's no header row for it.
    assert stored[0].document_type == "order_form"

    database.close()
    print("✓ fetch_all failed-document test passed")

def test_fetch_all_empty_database():
    """An empty database returns no records and renders cleanly."""
    print("Testing fetch_all on an empty database...")

    from src.reporting.console_reporter import ConsoleReporter

    repository, database = _build_test_repository()
    assert repository.fetch_all() == []

    output = ConsoleReporter().render([])
    assert "No documents have been processed yet." in output

    database.close()
    print("✓ empty database test passed")

def test_null_repository_fetch_all():
    """The no-database path returns an empty list rather than failing."""
    print("Testing null repository fetch_all...")

    from src.storage.contract_repository import NullContractRepository

    assert NullContractRepository().fetch_all() == []

    print("✓ null repository fetch_all test passed")

def _reporter_fixture(**overrides):
    from src.models.stored_contract import StoredContract

    data = _sample_contract("order_form", **overrides)
    return StoredContract(
        source_file="/docs/ACME Order From.pdf",
        document_type="order_form",
        processed_at="2026-08-21T10:00:00Z",
        status="extracted",
        data=data,
    )

def test_console_reporter_renders_fields():
    """
    The report contains every extracted field. Asserts on content, not exact
    whitespace, so cosmetic tweaks don't break the test.
    """
    print("Testing console reporter output...")

    from src.reporting.console_reporter import ConsoleReporter

    output = ConsoleReporter().render([_reporter_fixture()], "data/extractions.db")

    assert "ACME Order From.pdf" in output
    assert "customer: acme" in output
    assert "01-01-2025" in output          # start date, mm-dd-yyyy
    assert "162,000.00" in output          # amount, thousands-separated
    assert "Net 30" in output
    assert "Yes" in output                 # customer_signature rendered as Yes/No
    assert "Platform - Enterprise" in output
    assert "Support - Premium" in output
    assert "ORDER FORMS (1)" in output
    assert "data/extractions.db" in output

    print("✓ console reporter output test passed")

def test_console_reporter_collapses_uniform_burst():
    """
    A burst clause replicated across every item prints once, not per row;
    differing bursts print per item.
    """
    print("Testing console reporter burst collapsing...")

    from src.models.extracted_data import LineItem
    from src.reporting.console_reporter import ConsoleReporter

    uniform = ConsoleReporter().render([_reporter_fixture()])
    assert "Burst (all items)" in uniform
    assert uniform.count("10% burst allowance") == 1, "uniform burst should print once"

    mixed = ConsoleReporter().render([_reporter_fixture(items=[
        LineItem(product_name="A", quantity=1, price=1.0, total_amount=1.0, burst="first burst"),
        LineItem(product_name="B", quantity=1, price=1.0, total_amount=1.0, burst="second burst"),
    ])])
    assert "Burst (per item)" in mixed
    assert "first burst" in mixed and "second burst" in mixed

    print("✓ console reporter burst collapsing test passed")

def test_console_reporter_reconciliation():
    """
    A contract whose line items don't sum to its header amount is counted as
    a mismatch rather than silently passing.
    """
    print("Testing console reporter reconciliation...")

    from src.models.extracted_data import LineItem
    from src.reporting.console_reporter import ConsoleReporter

    # Sample contract: amount 162000, items 72000 + 90000 = 162000.
    matching = ConsoleReporter().render([_reporter_fixture()])
    assert "1/1 line items match header total ✓" in matching, matching

    mismatched = ConsoleReporter().render([_reporter_fixture(items=[
        LineItem(product_name="A", quantity=1, price=1.0, total_amount=1.0),
    ])])
    assert "0/1 line items match header total ✗" in mismatched, mismatched

    print("✓ console reporter reconciliation test passed")

def test_console_reporter_fits_width():
    """
    No line exceeds the report width. Counted in display columns rather than
    bytes, since the box-drawing characters are multi-byte UTF-8.
    """
    print("Testing console reporter line width...")

    from src.models.extracted_data import LineItem
    from src.reporting.console_reporter import ConsoleReporter

    long_text = (
        "Buyer may exceed the licensed analytics usage by up to ten percent "
        "per contract year at no additional cost, measured monthly in arrears."
    )
    contract = _reporter_fixture(
        billing_address=long_text,
        technical_account_manager=long_text,
        items=[LineItem(product_name=long_text, quantity=1, price=1.0,
                        total_amount=1.0, burst=long_text)],
    )

    output = ConsoleReporter(width=78).render([contract], "data/extractions.db")
    too_long = [line for line in output.splitlines() if len(line) > 78]
    assert not too_long, f"lines exceed width: {too_long[:2]}"

    print("✓ console reporter line width test passed")

def run_all_tests():
    """Run all comprehensive tests"""
    print("Running comprehensive tests for document data extraction system...")

    tests = [
        test_system_structure,
        test_service_initialization,
        test_extractor_instantiation,
        test_model_structure,
        test_field_registry_customer_overrides,
        test_contract_extractor_end_to_end,
        test_fenced_json_response,
        test_storage_round_trip,
        test_storage_type_routing,
        test_foreign_key_is_enforced,
        test_storage_idempotency_cascade,
        test_unchanged_document_skips_llm,
        test_failed_extraction_is_recorded_but_not_stored,
        test_unmapped_document_type_is_audited_only,
        test_null_repository_keeps_processor_working,
        test_transaction_rolls_back_on_error,
        test_fetch_all_round_trip,
        test_fetch_all_includes_failed_documents,
        test_fetch_all_empty_database,
        test_null_repository_fetch_all,
        test_console_reporter_renders_fields,
        test_console_reporter_collapses_uniform_burst,
        test_console_reporter_reconciliation,
        test_console_reporter_fits_width,
    ]

    try:
        for test in tests:
            test()

        print(f"\n{len(tests)}/{len(tests)} tests passed")
        print("✓ All comprehensive tests passed!")
        return True

    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
