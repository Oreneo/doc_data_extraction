"""
Comprehensive tests for the document data extraction system
"""

import json
import sys
import os
from pathlib import Path

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
    from src.models.extracted_data import BurstTerm, ExtractedContractData, ExtractedData, LineItem

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

    from src.models.extracted_data import BurstTerm, ExtractedContractData, ExtractedData, LineItem

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
        items=[LineItem(product_name="Widget", quantity=1, price=10.0, total_amount=10.0,
                        burst=BurstTerm(raw_text="10% burst"))],
    )
    assert contract_data.start_date == "01-01-2025", contract_data.start_date
    assert contract_data.end_date == "03-01-2025", contract_data.end_date
    assert contract_data.payment_terms == "Net 30", contract_data.payment_terms
    assert contract_data.items[0].burst.raw_text == "10% burst"

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
        "billing_address", "customer_signature", "signature_evidence",
        "technical_account_manager",
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
                "burst": {
                    "raw_text": "Buyer may exceed licensed analytics usage by up to 10% per contract year at no additional cost.",
                    "percentage": 10,
                    "basis": "licensed analytics usage",
                    "period": "per contract year",
                    "applies_to": "licensed analytics usage",
                },
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
    assert result.items[0].burst.percentage == 10
    assert result.items[0].burst.basis == "licensed analytics usage"

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
    from src.models.extracted_data import BurstTerm, ExtractedContractData, LineItem

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
                     total_amount=72000.0, burst=BurstTerm(raw_text="10% burst allowance",
                     percentage=10, basis="licensed units")),
            LineItem(product_name="Support - Premium", quantity=2, price=45000.0,
                     total_amount=90000.0, burst=BurstTerm(raw_text="10% burst allowance",
                     percentage=10, basis="licensed units")),
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
    assert items[0]["burst_raw_text"] == "10% burst allowance"
    assert items[0]["burst_percentage"] == 10

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

def test_every_run_extracts_from_scratch():
    """
    Stored results must never suppress work. Processing the same document
    twice calls the LLM twice.

    This is the inverse of a test that used to assert the opposite - that an
    unchanged document was skipped. The caching it guarded made runs
    non-reproducible: what a run did depended on what earlier runs had left
    in the database, and a quality check or retry has nothing to act on when
    extraction never happened.
    """
    print("Testing every run extracts from scratch...")

    from src.services.document_processor import DocumentProcessor

    _FakeClient = _fake_client_class()
    payload = json.dumps({
        "start_date": "01-01-2025", "end_date": "12-31-2025", "amount": 100,
        "payment_terms": "Net 30", "billing_address": "x",
        "customer_signature": False, "signature_evidence": None,
        "technical_account_manager": None,
        "items": [{"product_name": "W", "quantity": 1, "price": 100,
                   "total_amount": 100, "burst": None}],
    })

    calls = {"n": 0}

    class _CountingExtractor:
        def __init__(self, inner):
            self.inner = inner

        def extract(self, *args, **kwargs):
            calls["n"] += 1
            return self.inner.extract(*args, **kwargs)

    repository, database = _build_test_repository()
    inner, field_registry = _build_test_contract_extractor(client=_FakeClient(payload))
    processor = DocumentProcessor(_CountingExtractor(inner), field_registry, repository)

    fixture = os.path.join(FIXTURES, "unsigned_order_form.pdf")
    processor.process_file(fixture)
    processor.process_file(fixture)

    assert calls["n"] == 2, f"expected 2 extractions, got {calls['n']}"
    # ...and re-running updates in place rather than duplicating.
    rows = database.connection.execute(
        "SELECT COUNT(*) AS n FROM processed_documents").fetchone()["n"]
    assert rows == 1, f"expected 1 row after two runs, got {rows}"

    database.close()
    print("✓ every run extracts from scratch test passed")

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
    assert processor.repository.save(_sample_contract(), "/any.pdf", "any-hash") is None
    assert processor.repository.fetch_all() == []

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
    assert data.items[0].burst.raw_text == "10% burst allowance"
    assert data.items[0].burst.percentage == 10

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
    # The assignment specifies this field as True/False, not Yes/No.
    flat = " ".join(output.split())
    assert "Customer signature" in flat
    assert "True" in flat and "Yes" not in flat
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

    from src.models.extracted_data import BurstTerm, LineItem
    from src.reporting.console_reporter import ConsoleReporter

    # Every item carries the identical term, so it collapses to one entry
    # rather than repeating on each row.
    uniform = " ".join(ConsoleReporter().render([_reporter_fixture()]).split())
    assert "Burst (all items)" in uniform
    assert uniform.count("10% of licensed units") == 1, "uniform burst should print once"

    # Differing terms are printed against their own rows instead.
    mixed = " ".join(ConsoleReporter().render([_reporter_fixture(items=[
        LineItem(product_name="A", quantity=1, price=1.0, total_amount=1.0,
                 burst=BurstTerm(raw_text="first burst")),
        LineItem(product_name="B", quantity=1, price=1.0, total_amount=1.0,
                 burst=BurstTerm(raw_text="second burst")),
    ])]).split())
    assert "Burst (all items)" not in mixed
    assert "2 of 2 line items" in mixed
    assert "first burst" in mixed and "second burst" in mixed

    print("✓ console reporter burst collapsing test passed")

def test_console_reporter_reconciliation():
    """
    A contract whose line items don't sum to its header amount is counted as
    a mismatch rather than silently passing.
    """
    print("Testing console reporter reconciliation...")

    from src.models.extracted_data import BurstTerm, LineItem
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

    from src.models.extracted_data import BurstTerm, LineItem
    from src.reporting.console_reporter import ConsoleReporter

    long_text = (
        "Buyer may exceed the licensed analytics usage by up to ten percent "
        "per contract year at no additional cost, measured monthly in arrears."
    )
    contract = _reporter_fixture(
        billing_address=long_text,
        technical_account_manager=long_text,
        items=[LineItem(product_name=long_text, quantity=1, price=1.0,
                        total_amount=1.0, burst=BurstTerm(raw_text=long_text, basis=long_text))],
    )

    output = ConsoleReporter(width=78).render([contract], "data/extractions.db")
    too_long = [line for line in output.splitlines() if len(line) > 78]
    assert not too_long, f"lines exceed width: {too_long[:2]}"

    print("✓ console reporter line width test passed")

# --------------------------------------------------------------------------
# Structured burst term tests
# --------------------------------------------------------------------------

def _cloudshield_burst(percentage):
    from src.models.extracted_data import BurstTerm
    return BurstTerm(
        raw_text="During each consecutive 12 months ... up to the Burst Threshold ...",
        percentage=percentage,
        basis="CloudShield Enterprise Billable Units",
        cap_units=280000,
        period="per subscription year",
        applies_to="CloudShield Sensor",
    )

def _cloudshield_items():
    """Three Enterprise rows with different bursts, three Support rows with none."""
    from src.models.extracted_data import LineItem
    return [
        LineItem(product_name="CloudShield Enterprise", quantity=180000, price=3.25,
                 total_amount=7020000, term_months=12, price_period="monthly",
                 burst=_cloudshield_burst(38.89)),
        LineItem(product_name="Premium Support", quantity=1, price=22916.67,
                 total_amount=275000, term_months=12, price_period="monthly"),
        LineItem(product_name="CloudShield Enterprise", quantity=195000, price=3.25,
                 total_amount=7605000, term_months=12, price_period="monthly",
                 burst=_cloudshield_burst(28.21)),
        LineItem(product_name="Premium Support", quantity=1, price=22916.67,
                 total_amount=275000, term_months=12, price_period="monthly"),
        LineItem(product_name="CloudShield Enterprise", quantity=210000, price=3.25,
                 total_amount=8190000, term_months=12, price_period="monthly",
                 burst=_cloudshield_burst(19.05)),
        LineItem(product_name="Premium Support", quantity=1, price=22916.67,
                 total_amount=275000, term_months=12, price_period="monthly"),
    ]

def test_structured_burst_round_trip():
    """
    Every structured burst sub-field survives save -> fetch_all, and items
    without a burst come back with none - the per-item scoping the old
    string field couldn't express.
    """
    print("Testing structured burst round trip...")

    repository, database = _build_test_repository()
    repository.save(
        _sample_contract("order_form", amount=23640000.0, items=_cloudshield_items()),
        "/docs/cloudshield.pdf", "h-cs",
    )

    items = repository.fetch_all()[0].data.items
    assert len(items) == 6

    enterprise = [i for i in items if i.product_name == "CloudShield Enterprise"]
    support = [i for i in items if i.product_name == "Premium Support"]

    # Three DIFFERENT percentages, in document order - the case the old
    # "copy the same text to every item" behaviour flattened.
    assert [i.burst.percentage for i in enterprise] == [38.89, 28.21, 19.05]
    # Support lines carry no burst entitlement at all.
    assert all(i.burst is None for i in support)

    first = enterprise[0].burst
    assert first.basis == "CloudShield Enterprise Billable Units"
    assert first.cap_units == 280000
    assert first.period == "per subscription year"
    assert first.applies_to == "CloudShield Sensor"
    assert first.raw_text.startswith("During each consecutive 12 months")

    # Term/price basis explain why price x quantity != total_amount here.
    assert enterprise[0].term_months == 12
    assert enterprise[0].price_period == "monthly"
    assert enterprise[0].total_amount == 7020000

    database.close()
    print("✓ structured burst round trip test passed")

def test_burst_raw_text_only_is_non_regressive():
    """
    A burst whose structured parsing found nothing still round-trips its
    clause and renders it. This is the guarantee that the structured fields
    are strictly additive: worst case we have exactly the old behaviour.
    """
    print("Testing raw-text-only burst...")

    from src.models.extracted_data import BurstTerm, LineItem
    from src.reporting.console_reporter import ConsoleReporter

    clause = "Customer may utilize up to 5% more Billable Units at no additional cost."
    repository, database = _build_test_repository()
    repository.save(
        _sample_contract("order_form", amount=50000.0, items=[
            LineItem(product_name="SaaS Subscription", quantity=1, price=50000.0,
                     total_amount=50000.0, burst=BurstTerm(raw_text=clause)),
        ]),
        "/docs/acme.pdf", "h-acme",
    )

    stored = repository.fetch_all()
    burst = stored[0].data.items[0].burst
    assert burst is not None
    assert burst.raw_text == clause
    assert burst.percentage is None and burst.basis is None and burst.cap_units is None

    # And it still reaches the report rather than being dropped for lacking
    # structure.
    assert clause[:40] in " ".join(ConsoleReporter().render(stored).split())

    database.close()
    print("✓ raw-text-only burst test passed")

def test_reporter_renders_per_item_bursts():
    """Differing per-item bursts render against their own rows, not collapsed."""
    print("Testing per-item burst rendering...")

    from src.models.stored_contract import StoredContract
    from src.reporting.console_reporter import ConsoleReporter

    stored = StoredContract(
        source_file="/docs/cloudshield.pdf", document_type="order_form",
        processed_at="2026-08-22T09:00:00Z", status="extracted",
        data=_sample_contract("order_form", amount=23640000.0, items=_cloudshield_items()),
    )
    # Collapse whitespace: values wrap across lines at the report margin, so
    # asserting on raw output would be asserting on line-break positions.
    output = " ".join(ConsoleReporter().render([stored]).split())

    for percentage in ("38.89%", "28.21%", "19.05%"):
        assert percentage in output, percentage
    assert "cap 280,000" in output
    assert "applies to CloudShield Sensor" in output
    # Scope is legible: 3 of 6 lines carry a burst.
    assert "3 of 6 line items" in output

    print("✓ per-item burst rendering test passed")

def test_reporter_shows_ambiguous_scope_verbatim():
    """
    Where a clause names usage that doesn't map onto one product line
    (NovaFleet's "monitored workloads"), the document's own wording is shown
    rather than a product name we inferred - so an unresolved scope stays
    visible to the reader instead of being silently decided.
    """
    print("Testing ambiguous burst scope visibility...")

    from src.models.extracted_data import BurstTerm, LineItem
    from src.models.stored_contract import StoredContract
    from src.reporting.console_reporter import ConsoleReporter

    vague = BurstTerm(
        raw_text="Up to 15% additional monitored workloads allowed annually without charge.",
        percentage=15, basis="monitored workloads", period="annually",
        applies_to="monitored workloads",
    )
    items = [
        LineItem(product_name="Cloud Security Suite", quantity=1, price=120000.0,
                 total_amount=120000.0, burst=vague),
        LineItem(product_name="Advanced Threat Monitoring", quantity=3, price=18000.0,
                 total_amount=54000.0, burst=vague),
    ]
    stored = StoredContract(
        source_file="/docs/novafleet.pdf", document_type="purchase_order",
        processed_at="2026-08-22T09:00:00Z", status="extracted",
        data=_sample_contract("purchase_order", amount=174000.0, items=items),
    )

    output = " ".join(ConsoleReporter().render([stored]).split())
    assert "15% of monitored workloads" in output
    # The document's phrase, not a product name we picked for it.
    assert "applies to monitored workloads" in output
    assert "applies to Advanced Threat Monitoring" not in output

    print("✓ ambiguous burst scope test passed")

def test_additive_migration_preserves_existing_rows():
    """
    Opening a database created before the burst columns existed adds them
    without dropping data. This protects the stored content hashes: a
    destructive rebuild would force a full re-extraction against a
    rate-limited free tier.
    """
    print("Testing additive schema migration...")

    import sqlite3
    import tempfile

    from src.storage.database import Database

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "old.db")

        # A database on the OLD schema: items table with a plain `burst` TEXT
        # column and none of the new ones.
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE processed_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL UNIQUE,
                content_hash TEXT NOT NULL, document_type TEXT NOT NULL, customer_key TEXT,
                status TEXT NOT NULL, error TEXT, confidence REAL, raw_response TEXT,
                processed_at TEXT NOT NULL);
            CREATE TABLE sales_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL UNIQUE
                REFERENCES processed_documents (id) ON DELETE CASCADE, customer_key TEXT,
                start_date TEXT, end_date TEXT, amount REAL, payment_terms TEXT,
                billing_address TEXT, customer_signature INTEGER NOT NULL DEFAULT 0,
                technical_account_manager TEXT);
            CREATE TABLE sales_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, sales_order_id INTEGER NOT NULL
                REFERENCES sales_orders (id) ON DELETE CASCADE, line_number INTEGER NOT NULL,
                product_name TEXT NOT NULL, quantity REAL, price REAL, total_amount REAL,
                burst TEXT);
        """)
        old.execute("""INSERT INTO processed_documents
            (source_file, content_hash, document_type, status, processed_at)
            VALUES ('/docs/old.pdf', 'preserve-me', 'order_form', 'extracted', '2025-01-01')""")
        old.execute("""INSERT INTO sales_orders (document_id, amount) VALUES (1, 100.0)""")
        old.execute("""INSERT INTO sales_order_items
            (sales_order_id, line_number, product_name, quantity, price, total_amount, burst)
            VALUES (1, 1, 'Legacy Widget', 2, 50.0, 100.0, 'legacy burst text')""")
        old.commit()
        old.close()

        database = Database(path)

        columns = {r["name"] for r in
                   database.connection.execute("PRAGMA table_info(sales_order_items)")}
        for expected in ("burst_raw_text", "burst_percentage", "burst_cap_units",
                         "term_months", "price_period"):
            assert expected in columns, f"{expected} not added: {sorted(columns)}"

        # The pre-existing row survived, hash intact.
        row = database.connection.execute(
            "SELECT content_hash FROM processed_documents").fetchone()
        assert row["content_hash"] == "preserve-me"

        item = database.connection.execute(
            "SELECT * FROM sales_order_items").fetchone()
        assert item["product_name"] == "Legacy Widget"
        assert item["burst_raw_text"] is None     # new column, no value yet
        assert item["burst"] == "legacy burst text"   # old column left intact

        database.close()

    print("✓ additive schema migration test passed")

# --------------------------------------------------------------------------
# Path identity, report scoping, and progress tests
# --------------------------------------------------------------------------

def test_canonical_path_collapses_spellings():
    """
    Every spelling of one file resolves to a single stored identity. This is
    the direct regression test for the duplicate-row bug: a relative and an
    absolute path for the same document produced two rows, double-counting
    every aggregate built on them.
    """
    print("Testing canonical path resolution...")

    from src.services.document_processor import DocumentProcessor

    canonical = DocumentProcessor.canonical_path
    target = "sample_docs/ACME Order From.pdf"

    spellings = [
        target,
        "./" + target,
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), target),
        "sample_docs/../sample_docs/ACME Order From.pdf",
    ]
    resolved = {canonical(s) for s in spellings}
    assert len(resolved) == 1, f"spellings diverged: {resolved}"
    assert os.path.isabs(resolved.pop())

    print("✓ canonical path resolution test passed")

def test_migration_merges_duplicate_paths():
    """
    A database written before path canonicalization - holding the same
    document under an absolute and a relative path - is repaired on open:
    the newer row survives with a canonical path, and the older row's line
    items are cascaded away rather than orphaned.
    """
    print("Testing duplicate-path migration...")

    import sqlite3
    import tempfile

    from src.storage.database import Database

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "dupes.db")
        rel = "sample_docs/ACME Order From.pdf"
        abs_ = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)

        seed = Database(path)
        with seed.transaction() as conn:
            for i, (src, when, amount) in enumerate([
                (abs_, "2026-08-22T11:43:27", 152500.0),   # older
                (rel, "2026-08-22T13:00:53", 152500.0),    # newer - should win
            ], start=1):
                conn.execute(
                    """INSERT INTO processed_documents
                       (source_file, content_hash, document_type, status, processed_at)
                       VALUES (?, 'samehash', 'order_form', 'extracted', ?)""",
                    (src, when))
                conn.execute(
                    "INSERT INTO sales_orders (document_id, amount) VALUES (?, ?)",
                    (i, amount))
                conn.execute(
                    """INSERT INTO sales_order_items
                       (sales_order_id, line_number, product_name, quantity, price, total_amount)
                       VALUES (?, 1, 'SaaS Subscription', 1, ?, ?)""",
                    (i, amount, amount))
        # Both rows exist before the migration - the bug as observed.
        assert seed.connection.execute(
            "SELECT COUNT(*) FROM processed_documents").fetchone()[0] == 2
        assert seed.connection.execute(
            "SELECT SUM(amount) FROM sales_orders").fetchone()[0] == 305000.0
        seed.close()

        repaired = Database(path)

        rows = repaired.connection.execute(
            "SELECT source_file, processed_at FROM processed_documents").fetchall()
        assert len(rows) == 1, f"expected 1 row after merge, got {len(rows)}"
        # The newer extraction survived, under a canonical path.
        assert rows[0]["processed_at"] == "2026-08-22T13:00:53"
        assert rows[0]["source_file"] == str(Path(rel).resolve())

        # Aggregates are correct again, and no orphaned children remain.
        assert repaired.connection.execute(
            "SELECT SUM(amount) FROM sales_orders").fetchone()[0] == 152500.0
        assert repaired.connection.execute(
            "SELECT COUNT(*) FROM sales_order_items").fetchone()[0] == 1

        repaired.close()

    print("✓ duplicate-path migration test passed")

def test_migration_leaves_distinct_documents_alone():
    """The migration must never merge two genuinely different documents."""
    print("Testing migration is conservative...")

    import tempfile

    from src.storage.database import Database

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "distinct.db")
        seed = Database(path)
        with seed.transaction() as conn:
            for src in ("/docs/a.pdf", "/docs/b.pdf"):
                conn.execute(
                    """INSERT INTO processed_documents
                       (source_file, content_hash, document_type, status, processed_at)
                       VALUES (?, 'h', 'order_form', 'extracted', '2026-01-01')""",
                    (src,))
        seed.close()

        repaired = Database(path)
        assert repaired.connection.execute(
            "SELECT COUNT(*) FROM processed_documents").fetchone()[0] == 2
        repaired.close()

    print("✓ migration conservatism test passed")

def test_report_scopes_to_this_run():
    """
    Running one document reports that document, not everything ever stored -
    while still accounting for the rest in a "not shown" line.
    """
    print("Testing run-scoped reporting...")

    from src.models.stored_contract import StoredContract
    from src.reporting.console_reporter import ConsoleReporter

    def stored(name, status="extracted", with_data=True):
        return StoredContract(
            source_file=f"/docs/{name}", document_type="order_form",
            processed_at="2026-08-22T13:00:00Z", status=status,
            error=None if status == "extracted" else "some earlier failure",
            data=_sample_contract("order_form") if with_data else None,
        )

    contracts = [
        stored("wanted.pdf"),
        stored("old1.pdf"),
        stored("old2.pdf", status="failed", with_data=False),
        stored("old3.pdf", status="failed", with_data=False),
    ]

    scoped = " ".join(ConsoleReporter().render(
        contracts, "data/extractions.db", only_paths={"/docs/wanted.pdf"}).split())

    assert "wanted.pdf" in scoped
    for hidden in ("old1.pdf", "old2.pdf", "old3.pdf"):
        assert hidden not in scoped, f"{hidden} should not be shown"
    assert "1 document" in scoped
    assert "3 other documents stored" in scoped
    assert "1 extracted" in scoped and "2 failed" in scoped

    # only_paths=None keeps the render-everything behaviour.
    everything = " ".join(ConsoleReporter().render(contracts, None).split())
    assert "old1.pdf" in everything
    assert "Not shown" not in everything

    print("✓ run-scoped reporting test passed")

def test_progress_reporter_output():
    """Progress narrates the run; the null reporter stays silent."""
    print("Testing progress reporter...")

    import io

    from src.reporting.progress_reporter import (
        ConsoleProgressReporter,
        NullProgressReporter,
    )

    stream = io.StringIO()
    p = ConsoleProgressReporter(stream)
    p.run_started("sample_docs", 2, "openrouter_paid", "anthropic/claude-sonnet-5")
    p.document_started(1, 2, "/docs/ACME Order From.pdf")
    p.text_extracted(2, 2180)
    p.identified("order_form", "acme")
    p.llm_call_started()
    p.llm_call_finished(4.2)
    p.document_stored(5, None)
    p.document_started(2, 2, "/docs/Other.pdf")
    out = stream.getvalue()

    assert "Processing 2 documents in sample_docs" in out
    assert "anthropic/claude-sonnet-5" in out
    assert "[1/2] ACME Order From.pdf" in out
    assert "2 pages, 2,180 chars" in out
    assert "order_form · customer: acme" in out
    # The slow step is announced before it happens, not only after.
    assert out.index("calling model") < out.index("responded in")
    assert "stored 5 line items" in out

    silent = io.StringIO()
    n = NullProgressReporter()
    n.run_started("x", 1, "p", "m"); n.document_started(1, 1, "y")
    assert silent.getvalue() == ""

    print("✓ progress reporter test passed")

def test_delete_removes_document_and_children():
    """
    Deleting a stored document removes it and everything beneath it.

    Every run re-extracts regardless, so this is for clearing out records of
    documents that have left the input folder - not for forcing a refresh.
    """
    print("Testing delete removes document and children...")

    repository, database = _build_test_repository()
    repository.save(_sample_contract(), "/docs/acme.pdf", "hash-1")

    assert len(repository.fetch_all()) == 1
    assert repository.delete("/docs/acme.pdf") is True
    assert repository.fetch_all() == []

    # Header and line items cascaded away rather than being orphaned.
    for table in ("processed_documents", "sales_orders", "sales_order_items"):
        count = database.connection.execute(
            f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        assert count == 0, f"{table} still has {count} rows"

    # Deleting something absent is a no-op, not an error.
    assert repository.delete("/docs/never-stored.pdf") is False

    database.close()
    print("✓ delete removes document and children test passed")

def test_delete_all_and_null_repository():
    """delete_all clears everything; the null repository stays a no-op."""
    print("Testing delete_all...")

    from src.storage.contract_repository import NullContractRepository

    repository, database = _build_test_repository()
    repository.save(_sample_contract("order_form"), "/docs/a.pdf", "h1")
    repository.save(_sample_contract("purchase_order"), "/docs/b.pdf", "h2")

    assert repository.delete_all() == 2
    assert repository.fetch_all() == []
    assert database.connection.execute(
        "SELECT COUNT(*) AS n FROM sales_order_items").fetchone()["n"] == 0

    null = NullContractRepository()
    assert null.delete("/docs/a.pdf") is False
    assert null.delete_all() == 0

    database.close()
    print("✓ delete_all test passed")

def test_reset_utility_matches_by_fragment():
    """
    The reset tool resolves a name fragment to a stored path, so you don't
    have to type the full path (and the path spelling doesn't matter).
    """
    print("Testing reset utility matching...")

    from src.utils.reset_documents import _match

    stored = [
        "/Users/x/Dev/proj/sample_docs/ACME Order From.pdf",
        "/Users/x/Dev/proj/sample_docs/CloudShield Order Form.pdf",
    ]

    assert _match(stored, "ACME") == [stored[0]]
    assert _match(stored, "acme") == [stored[0]], "matching should be case-insensitive"
    assert _match(stored, "CloudShield") == [stored[1]]
    assert _match(stored, "Order") == stored, "a broad fragment matches both"
    assert _match(stored, "nonexistent") == []

    print("✓ reset utility matching test passed")

def test_signature_evidence_round_trip():
    """
    signature_evidence survives storage and reaches the report, so a
    true/false can be checked without reopening the PDF.
    """
    print("Testing signature evidence round trip...")

    from src.reporting.console_reporter import ConsoleReporter

    repository, database = _build_test_repository()
    evidence = "signature line blank, Name/Title/Date filled in"
    repository.save(
        _sample_contract("order_form", customer_signature=False,
                         signature_evidence=evidence),
        "/docs/acme.pdf", "h-sig",
    )

    stored = repository.fetch_all()
    assert stored[0].data.customer_signature is False
    assert stored[0].data.signature_evidence == evidence

    flat = " ".join(ConsoleReporter().render(stored).split())
    assert "Signature evidence" in flat
    assert "signature line blank" in flat

    database.close()
    print("✓ signature evidence round trip test passed")

def test_signature_renders_as_true_false():
    """
    The assignment specifies Customer signature as True/False. Yes/No must
    not appear.
    """
    print("Testing signature True/False rendering...")

    from src.reporting.console_reporter import ConsoleReporter

    signed = " ".join(ConsoleReporter().render(
        [_reporter_fixture(customer_signature=True)]).split())
    unsigned = " ".join(ConsoleReporter().render(
        [_reporter_fixture(customer_signature=False)]).split())

    assert "Customer signature ... True" in signed or "True" in signed
    assert "False" in unsigned
    for output in (signed, unsigned):
        assert "Yes" not in output and " No " not in output

    print("✓ signature True/False rendering test passed")

def test_acme_unsigned_case_end_to_end():
    """
    ACME's exact shape - blank signature line, Name/Title/Date filled - must
    come out False. This is the regression test for the field definition
    that previously said a filled name block counted as a signature.

    Also covers the opposite direction with BrightOps's explicit mark, so
    the stricter rule doesn't swing the error the other way.
    """
    print("Testing ACME unsigned / BrightOps signed cases...")

    _FakeClient = _fake_client_class()

    def payload(signed, evidence):
        return json.dumps({
            "start_date": "01-01-2025", "end_date": "12-31-2026",
            "amount": 152500, "payment_terms": "Net 30",
            "billing_address": "123 Main Street, New York, NY, USA",
            "customer_signature": signed,
            "signature_evidence": evidence,
            "technical_account_manager": None,
            "items": [{"product_name": "SaaS Subscription", "quantity": 1,
                       "price": 50000, "total_amount": 50000, "burst": None}],
        })

    acme, _ = _build_test_contract_extractor(client=_FakeClient(
        payload(False, "signature line blank, Name/Title/Date filled in")))
    result = acme.extract(text="...", document_type="order_form")
    assert result.customer_signature is False, "blank signature line must be False"
    assert "blank" in result.signature_evidence

    brightops, _ = _build_test_contract_extractor(client=_FakeClient(
        payload(True, "'✔ Signed' beside Buyer Signature")))
    result = brightops.extract(text="...", document_type="purchase_order")
    assert result.customer_signature is True, "explicit mark must stay True"
    assert "Signed" in result.signature_evidence

    print("✓ ACME/BrightOps signature cases test passed")

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

def test_signature_ink_detects_drawn_signature():
    """
    Two PDFs with identical text, differing only in a drawn curve over the
    customer's signature rule. Text extraction cannot tell them apart; the
    geometric scan must.
    """
    print("Testing signature ink detection...")

    from src.utils.signature_ink import SignatureInkDetector

    detector = SignatureInkDetector()

    signed = detector.detect(os.path.join(FIXTURES, "signed_order_form.pdf"))
    assert signed.found is True, "a drawn signature must be detected"
    assert signed.anchors_examined == 2
    assert "drawing" in signed.detail, signed.detail

    unsigned = detector.detect(os.path.join(FIXTURES, "unsigned_order_form.pdf"))
    assert unsigned.found is False, "an empty signature line must not be flagged"
    assert unsigned.anchors_examined == 2

    print("✓ signature ink detection test passed")

def test_signature_ink_does_not_bleed_across_columns():
    """
    The fixture is signed only in the customer's (right-hand) column. The
    band must stop at the neighbouring signature label, or one party's
    signature gets credited to the other.
    """
    print("Testing signature column isolation...")

    from src.utils.signature_ink import SignatureInkDetector

    finding = SignatureInkDetector().detect(
        os.path.join(FIXTURES, "signed_order_form.pdf"))

    # The customer's label sits at x=320, the vendor's at x=72. Only the
    # customer's column should be reported.
    assert "x=320" in finding.detail, finding.detail
    assert "x=72" not in finding.detail, f"bled into vendor column: {finding.detail}"

    print("✓ signature column isolation test passed")

def test_signature_ink_detects_annotation_signatures():
    """
    Signatures added by a PDF viewer are ANNOTATIONS, not page content.
    macOS Preview writes a /Stamp for a drawn or scanned signature and a
    /FreeText for a typed one, and neither appears in page.images,
    page.curves, or extract_text() - all three CloudShield variants produce
    byte-identical text. Without annotation support every such signature is
    missed, which is the exact false negative this detector exists to
    prevent.
    """
    print("Testing annotation signature detection...")

    from src.utils.signature_ink import SignatureInkDetector

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = os.path.join(root, "sample_docs")
    detector = SignatureInkDetector()

    stamped = detector.detect(os.path.join(docs, "CloudShield Order Form signed image.pdf"))
    assert stamped.found is True, "a /Stamp signature annotation must be detected"
    assert "Stamp" in stamped.detail

    typed = detector.detect(os.path.join(docs, "CloudShield Order Form signed text.pdf"))
    assert typed.found is True, "a /FreeText signature annotation must be detected"
    assert "FreeText" in typed.detail
    # The annotation's text is quoted, giving the model something concrete to
    # weigh. Asserting on the quoting, not the name - the fixture's signature
    # text is the user's to change.
    assert "'" in typed.detail, f"annotation content should be quoted: {typed.detail}"

    # The unmodified original must stay unsigned - the three files differ
    # only in the annotation, so this is what proves the signal is real.
    original = detector.detect(os.path.join(docs, "CloudShield Order Form.pdf"))
    assert original.found is False, "the unsigned original must not be flagged"

    print("✓ annotation signature detection test passed")

def test_signature_ink_attributes_to_correct_party():
    """
    Both signed CloudShield files are signed in the CUSTOMER's column
    (TechNova, x=312), not the vendor's (CloudShield, x=96). The FreeText
    annotation actually starts at x=305 - left of its own label - so an
    any-overlap rule credits it to the vendor as well. Ink must be
    attributed to the single area it overlaps most.
    """
    print("Testing signature party attribution...")

    from src.utils.signature_ink import SignatureInkDetector

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = os.path.join(root, "sample_docs")
    detector = SignatureInkDetector()

    for name in ("CloudShield Order Form signed image.pdf",
                 "CloudShield Order Form signed text.pdf"):
        detail = detector.detect(os.path.join(docs, name)).detail
        assert "x=312" in detail, f"{name}: expected customer column, got {detail}"
        assert "x=96" not in detail, f"{name}: bled into vendor column: {detail}"

    print("✓ signature party attribution test passed")

def test_signing_a_document_changes_its_content_hash():
    """
    Signing a document in place must invalidate its cached hash.

    Signatures are added as PDF annotations, which do not appear in the
    extracted text: the three CloudShield variants produce byte-identical
    text. With a text-only hash, signing a document in place would leave the
    fingerprint unchanged, the next run would skip it, and the signature
    would never be seen - the caching would hide the very thing the
    signature scan exists to find.
    """
    print("Testing signing changes the content hash...")

    import pdfplumber

    from src.services.document_processor import DocumentProcessor
    from src.utils.signature_ink import SignatureInkDetector

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = os.path.join(root, "sample_docs")
    detector = SignatureInkDetector()

    def text_of(path):
        with pdfplumber.open(path) as pdf:
            return "".join((p.extract_text() or "") + "\n" for p in pdf.pages)

    names = ["CloudShield Order Form.pdf",
             "CloudShield Order Form signed image.pdf",
             "CloudShield Order Form signed text.pdf"]
    paths = [os.path.join(docs, n) for n in names]

    # Precondition: the text really is identical, so the test is exercising
    # the signature component of the hash and nothing else.
    texts = {text_of(p) for p in paths}
    assert len(texts) == 1, "expected identical extracted text across variants"

    hashes = {DocumentProcessor._content_hash(text_of(p), detector.detect(p)) for p in paths}
    assert len(hashes) == 3, f"signed and unsigned must hash differently, got {hashes}"

    print("✓ signing changes content hash test passed")

def test_signature_ink_no_false_positive_on_real_samples():
    """
    None of the real sample documents is signed with ink, and ACME carries a
    letterhead logo. A naive "does this PDF contain images?" check would
    flag ACME; the positional band must not.
    """
    print("Testing no false positives on real samples...")

    import glob

    from src.utils.signature_ink import SignatureInkDetector

    detector = SignatureInkDetector()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for path in glob.glob(os.path.join(root, "sample_docs", "*.pdf")):
        # The two "signed" CloudShield variants genuinely carry a signature
        # annotation; they are covered by the annotation tests above.
        if "signed" in os.path.basename(path).lower():
            continue
        finding = detector.detect(path)
        assert finding.found is False, f"false positive on {os.path.basename(path)}"
        assert finding.anchors_examined > 0, (
            f"no signature area found in {os.path.basename(path)} - "
            "anchoring is broken")

    print("✓ no false positives test passed")

def test_signature_hint_reaches_the_prompt():
    """
    A positive finding must actually reach the model. The model cannot see
    images, so this hint is the only way a drawn signature can influence the
    extraction.
    """
    print("Testing signature hint reaches the prompt...")

    from src.utils.signature_ink import SignatureInkFinding

    found = SignatureInkFinding(found=True, detail="1 curves object(s) beside "
                                "'Signature:' at x=320 on page 1", anchors_examined=2)
    hint = found.as_prompt_hint()
    assert "x=320" in hint
    assert "signed" in hint.lower()

    absent = SignatureInkFinding(found=False, anchors_examined=2).as_prompt_hint()
    assert "no drawing or image content" in absent

    # No signature area at all is distinct from "found nothing".
    none = SignatureInkFinding(found=False, anchors_examined=0).as_prompt_hint()
    assert "no signature area" in none.lower()

    # And the rendered prompt carries it through.
    from src.config.field_registry import FieldDefinitionRegistry
    from src.prompts.prompt_repository import PromptRepository
    from src.prompts.schema_prompt_builder import SchemaPromptBuilder

    fields = FieldDefinitionRegistry().get_fields(None)
    prompt = PromptRepository().render(
        "extract_contract_fields",
        fields_description=SchemaPromptBuilder.build_fields_description(fields),
        json_example=SchemaPromptBuilder.build_json_example(fields),
        signature_hint=hint,
        retry_note="",
        document_text="...")
    assert "x=320" in prompt

    print("✓ signature hint test passed")

def test_signature_detector_never_breaks_extraction():
    """A scan failure must degrade to 'no finding', never raise."""
    print("Testing signature detector resilience...")

    from src.utils.signature_ink import SignatureInkDetector

    finding = SignatureInkDetector().detect("/does/not/exist.pdf")
    assert finding.found is False
    assert finding.anchors_examined == 0

    print("✓ signature detector resilience test passed")

def _burstless_payload(with_burst=False):
    burst = ({"raw_text": "Burst Threshold up to 10%", "percentage": 10} if with_burst else None)
    return json.dumps({
        "start_date": "01-01-2025", "end_date": "12-31-2025", "amount": 100,
        "payment_terms": "Net 30", "billing_address": "x",
        "customer_signature": False, "signature_evidence": None,
        "technical_account_manager": None,
        "items": [{"product_name": "W", "quantity": 1, "price": 100,
                   "total_amount": 100, "burst": burst}],
    })

def test_quality_check_flags_dropped_burst():
    """
    The real failure: a document whose text mentions burst, extracted with
    no burst on any item. Nothing else about the result looks wrong, which
    is precisely why it needs flagging.
    """
    print("Testing quality check flags dropped burst...")

    from src.models.extracted_data import BurstTerm, ExtractedContractData, LineItem
    from src.services.quality_checker import ExtractionQualityChecker

    checker = ExtractionQualityChecker()
    text = "... up to the Burst Threshold at no additional cost ..."

    dropped = ExtractedContractData(document_type="order_form", items=[
        LineItem(product_name="W", quantity=1, price=1.0, total_amount=1.0)])
    assert checker.check(text, dropped), "a dropped burst must warn"

    kept = ExtractedContractData(document_type="order_form", items=[
        LineItem(product_name="W", quantity=1, price=1.0, total_amount=1.0,
                 burst=BurstTerm(raw_text="Burst Threshold up to 10%"))])
    assert checker.check(text, kept) == [], "an extracted burst must not warn"

    # A document with no burst clause must never warn, however it extracts.
    assert checker.check("no such clause here", dropped) == []

    # A failed extraction already reports its error; don't pile on.
    failed = ExtractedContractData(document_type="order_form", error="boom", items=[])
    assert checker.check(text, failed) == []

    print("✓ quality check flags dropped burst test passed")

def test_quality_check_no_false_positives_on_real_documents():
    """
    A noisy check gets ignored, which is worse than no check. Replays every
    sample document's text against a result that DOES carry burst terms -
    none may warn.
    """
    print("Testing quality check has no false positives...")

    import glob

    import pdfplumber

    from src.models.extracted_data import BurstTerm, ExtractedContractData, LineItem
    from src.services.quality_checker import ExtractionQualityChecker

    checker = ExtractionQualityChecker()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    good = ExtractedContractData(document_type="order_form", items=[
        LineItem(product_name="W", quantity=1, price=1.0, total_amount=1.0,
                 burst=BurstTerm(raw_text="Burst Threshold"))])

    for path in glob.glob(os.path.join(root, "sample_docs", "*.pdf")):
        with pdfplumber.open(path) as pdf:
            text = " ".join((p.extract_text() or "") for p in pdf.pages)
        assert checker.check(text, good) == [], f"false positive on {os.path.basename(path)}"

    print("✓ quality check false-positive test passed")

def test_retry_fires_once_and_only_on_a_warning():
    """
    A detected drop gets exactly one more attempt; a clean extraction gets
    none. Both halves matter - an unbounded retry burns money, and retrying
    a good result burns it for nothing.
    """
    print("Testing retry behaviour...")

    from src.models.extracted_data import BurstTerm, ExtractedContractData, LineItem
    from src.services.document_processor import DocumentProcessor

    def item(with_burst):
        return LineItem(product_name="W", quantity=1, price=1.0, total_amount=1.0,
                        burst=BurstTerm(raw_text="Burst Threshold") if with_burst else None)

    class _ScriptedExtractor:
        """Hands back scripted results, recording the retry note each time."""
        def __init__(self, *with_burst):
            self.script = list(with_burst)
            self.notes = []

        def extract(self, text, document_type, customer_key=None,
                    signature_hint=None, retry_note=""):
            self.notes.append(retry_note)
            assert self.script, "extractor called more times than scripted"
            return ExtractedContractData(document_type=document_type,
                                         items=[item(self.script.pop(0))])

    _, registry = _build_test_contract_extractor()
    text = "... up to the Burst Threshold at no additional cost ..."

    # 1. First attempt drops burst; retry recovers it.
    scripted = _ScriptedExtractor(False, True)
    processor = DocumentProcessor(scripted, registry)
    first = scripted.extract(text, "order_form")
    scripted.notes.clear()
    result = processor._check_and_maybe_retry(text, first, "order_form", None, None)

    assert len(scripted.notes) == 1, f"expected exactly one retry, got {len(scripted.notes)}"
    # The retry prompt must DIFFER - at temperature 0 an identical prompt
    # returns the identical wrong answer, so the note is what makes it work.
    assert scripted.notes[0] != "", "retry must carry a corrective note"
    assert "burst" in scripted.notes[0].lower()
    assert result.items[0].burst is not None, "retry result should be kept"
    assert result.warnings and "resolved on retry" in result.warnings[0]

    # 2. A clean first attempt triggers no retry at all.
    scripted = _ScriptedExtractor(True)
    processor = DocumentProcessor(scripted, registry)
    good = scripted.extract(text, "order_form")
    scripted.notes.clear()
    result = processor._check_and_maybe_retry(text, good, "order_form", None, None)
    assert scripted.notes == [], "a clean extraction must not be retried"
    assert result.warnings == []

    # 3. If the retry also fails, keep the FIRST result and its warning
    #    rather than assuming the second attempt is better. Exactly two
    #    attempts, never a loop.
    scripted = _ScriptedExtractor(False, False)
    processor = DocumentProcessor(scripted, registry)
    first = scripted.extract(text, "order_form")
    scripted.notes.clear()
    result = processor._check_and_maybe_retry(text, first, "order_form", None, None)
    assert len(scripted.notes) == 1, "must not retry more than once"
    assert result.warnings and "resolved" not in result.warnings[0]

    print("✓ retry behaviour test passed")

def test_burst_headline_degrades_when_basis_is_missing():
    """
    A percentage alone says nothing useful - "15%" of what? The model does
    not reliably fill `basis` (NovaFleet's "monitored workloads" landed in
    `applies_to` on one run and `basis` on another), so a missing basis must
    fall back to the clause rather than rendering a bare number.
    """
    print("Testing burst headline fallback...")

    from src.models.extracted_data import BurstTerm
    from src.reporting.console_reporter import ConsoleReporter

    reporter = ConsoleReporter()
    clause = "Burst (Item Level): Up to 15% additional monitored workloads allowed annually."

    # Best case: percentage + basis.
    full = reporter._burst_headline(
        BurstTerm(raw_text=clause, percentage=15, basis="monitored workloads"))
    assert full == "15% of monitored workloads"

    # Basis missing - must not stop at the number.
    partial = reporter._burst_headline(BurstTerm(raw_text=clause, percentage=15))
    assert partial != "15%", "a bare percentage is uninformative"
    assert "15%" in partial and "monitored workloads" in partial

    # Nothing parsed at all - the clause itself.
    bare = reporter._burst_headline(BurstTerm(raw_text=clause))
    assert bare == clause

    print("✓ burst headline fallback test passed")

def test_warnings_round_trip_and_render():
    """Warnings survive storage and appear against the document."""
    print("Testing warnings round trip...")

    from src.reporting.console_reporter import ConsoleReporter

    repository, database = _build_test_repository()
    contract = _sample_contract("order_form")
    contract.warnings = ["document text mentions burst terms but none were extracted"]
    repository.save(contract, "/docs/x.pdf", "h")

    stored = repository.fetch_all()
    assert stored[0].warnings == contract.warnings

    flat = " ".join(ConsoleReporter().render(stored).split())
    assert "Warning" in flat
    assert "mentions burst terms" in flat
    assert "1 document with a possible dropped field" in flat

    # A clean document renders no warning furniture.
    repository.delete("/docs/x.pdf")
    repository.save(_sample_contract("order_form"), "/docs/y.pdf", "h2")
    clean = " ".join(ConsoleReporter().render(repository.fetch_all()).split())
    assert "Warning" not in clean
    assert "Quality warnings" not in clean

    database.close()
    print("✓ warnings round trip test passed")

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
        test_every_run_extracts_from_scratch,
        test_quality_check_flags_dropped_burst,
        test_quality_check_no_false_positives_on_real_documents,
        test_retry_fires_once_and_only_on_a_warning,
        test_burst_headline_degrades_when_basis_is_missing,
        test_warnings_round_trip_and_render,
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
        test_structured_burst_round_trip,
        test_burst_raw_text_only_is_non_regressive,
        test_reporter_renders_per_item_bursts,
        test_reporter_shows_ambiguous_scope_verbatim,
        test_additive_migration_preserves_existing_rows,
        test_canonical_path_collapses_spellings,
        test_migration_merges_duplicate_paths,
        test_migration_leaves_distinct_documents_alone,
        test_report_scopes_to_this_run,
        test_progress_reporter_output,
        test_delete_removes_document_and_children,
        test_delete_all_and_null_repository,
        test_reset_utility_matches_by_fragment,
        test_signature_evidence_round_trip,
        test_signature_renders_as_true_false,
        test_acme_unsigned_case_end_to_end,
        test_signature_ink_detects_drawn_signature,
        test_signature_ink_does_not_bleed_across_columns,
        test_signature_ink_detects_annotation_signatures,
        test_signature_ink_attributes_to_correct_party,
        test_signing_a_document_changes_its_content_hash,
        test_signature_ink_no_false_positive_on_real_samples,
        test_signature_hint_reaches_the_prompt,
        test_signature_detector_never_breaks_extraction,
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
