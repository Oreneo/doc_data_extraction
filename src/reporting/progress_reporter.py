"""
Live progress output while documents are being processed.

Separate from ConsoleReporter, which renders the finished result: this
narrates the run as it happens, so the minutes spent waiting on LLM calls
aren't a blank screen.
"""

import os
import sys
import time
from typing import Optional


class NullProgressReporter:
    """
    Reports nothing.

    The default, so DocumentProcessor needs no `if verbose:` branches and
    the test suite stays quiet without special-casing. Same null-object
    pattern as NullContractRepository.
    """

    def run_started(self, target: str, count: int, profile: str, model: str) -> None:
        pass

    def document_started(self, index: int, total: int, path: str) -> None:
        pass

    def text_extracted(self, pages: int, characters: int) -> None:
        pass

    def identified(self, document_type: str, customer_key: Optional[str]) -> None:
        pass

    def signature_scanned(self, found: bool, anchors: int) -> None:
        pass

    def quality_warning(self, warnings, retrying: bool) -> None:
        pass

    def llm_call_started(self, retry: bool = False) -> None:
        pass

    def llm_call_finished(self, seconds: float) -> None:
        pass

    def llm_retry(self, attempt: int, max_attempts: int, error: str, delay: float) -> None:
        pass

    def document_stored(self, item_count: int, error: Optional[str]) -> None:
        pass

    def run_finished(self) -> None:
        pass


class ConsoleProgressReporter(NullProgressReporter):
    """
    Writes progress to a stream as the run proceeds.

    Every write is flushed: Python block-buffers stdout when it isn't a
    terminal, so without this a redirected run (`> log.txt`, or a CI job)
    shows nothing until the process exits - which makes a slow run
    indistinguishable from a hung one.
    """

    def __init__(self, stream=None):
        """
        Args:
            stream: Where to write. Defaults to stdout.
        """
        self.stream = stream or sys.stdout

    def _write(self, text: str) -> None:
        self.stream.write(text + "\n")
        self.stream.flush()

    def run_started(self, target: str, count: int, profile: str, model: str) -> None:
        noun = "document" if count == 1 else "documents"
        self._write(f"Processing {count} {noun} in {target}")
        self._write(f"  profile: {profile} · model: {model}")

    def document_started(self, index: int, total: int, path: str) -> None:
        self._write("")
        self._write(f"  [{index}/{total}] {os.path.basename(path)}")

    def text_extracted(self, pages: int, characters: int) -> None:
        page_noun = "page" if pages == 1 else "pages"
        self._write(f"        text extracted ({pages} {page_noun}, {characters:,} chars)")

    def identified(self, document_type: str, customer_key: Optional[str]) -> None:
        self._write(f"        {document_type} · customer: {customer_key or 'unrecognized'}")

    def signature_scanned(self, found: bool, anchors: int) -> None:
        if not anchors:
            self._write("        signature scan: no signature area found in layout")
        elif found:
            self._write("        signature scan: DRAWN/IMAGE MARK FOUND in signature area")
        else:
            self._write(f"        signature scan: no drawn mark ({anchors} area(s) checked)")

    def quality_warning(self, warnings, retrying: bool) -> None:
        for warning in warnings:
            suffix = " - retrying" if retrying else " - retry did not help"
            self._write(f"        quality check: {warning}{suffix}")

    def llm_call_started(self, retry: bool = False) -> None:
        # Announced *before* the call, so the wait that follows is labelled
        # rather than mysterious.
        self._write(f"        calling model{' (retry)' if retry else ''} ...")

    def llm_call_finished(self, seconds: float) -> None:
        self._write(f"        model responded in {seconds:.1f}s")

    def llm_retry(self, attempt: int, max_attempts: int, error: str, delay: float) -> None:
        self._write(
            f"        retry {attempt}/{max_attempts} after {error} "
            f"(waiting {delay:.0f}s)"
        )

    def document_stored(self, item_count: int, error: Optional[str]) -> None:
        if error:
            self._write(f"        FAILED: {error}")
        else:
            noun = "line item" if item_count == 1 else "line items"
            self._write(f"        stored {item_count} {noun}")

    def run_finished(self) -> None:
        self._write("")


class Timer:
    """Small context manager for timing a step."""

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.seconds = time.monotonic() - self._start
        return False
