"""
Smoke tests for report/pdf_builder.py.

Verifies that PDF generation does not raise exceptions and produces
a non-empty file. Does not validate PDF content visually.
"""

from __future__ import annotations

import os
import tempfile


from report.pdf_builder import build_pdf


class TestPDFBuilderSmoke:
    def test_build_pdf_creates_file(self, sample_scan_result):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            build_pdf(sample_scan_result, tmp_path)
            assert os.path.exists(tmp_path)
            assert os.path.getsize(tmp_path) > 1024  # at least 1KB
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_build_pdf_with_no_findings(self, sample_scan_result):
        """PDF generation should succeed even with an empty findings list."""
        sample_scan_result.findings = []
        sample_scan_result.recommendations = []

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            build_pdf(sample_scan_result, tmp_path)
            assert os.path.exists(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_build_pdf_with_no_cost_trends(self, sample_scan_result):
        """PDF generation should succeed when Cost Explorer data is unavailable."""
        sample_scan_result.cost_trends = []
        sample_scan_result.top_billed_services = []
        sample_scan_result.uncovered_high_spend = []

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            build_pdf(sample_scan_result, tmp_path)
            assert os.path.exists(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_build_pdf_with_no_forecast(self, sample_scan_result):
        """PDF generation should succeed when no forecast is available."""
        sample_scan_result.forecast = None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            build_pdf(sample_scan_result, tmp_path)
            assert os.path.exists(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
