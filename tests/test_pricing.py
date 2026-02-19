"""
Unit tests for utils/pricing.py.

Uses unittest.mock to simulate Pricing API responses and verify
the fallback chain works correctly.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from utils.pricing import PricingClient, get_pricing_client, reset_pricing_client


def _make_price_response(price: str) -> dict:
    """Build a minimal Pricing API get_products() response."""
    product = {
        "terms": {
            "OnDemand": {
                "TERM_KEY": {
                    "priceDimensions": {
                        "DIM_KEY": {
                            "pricePerUnit": {"USD": price}
                        }
                    }
                }
            }
        }
    }
    return {"PriceList": [json.dumps(product)]}


class TestPricingClientFallback:
    def test_ec2_fallback_when_api_unavailable(self):
        """When the Pricing API is unavailable, fallback prices are used."""
        session = MagicMock()
        session.client.side_effect = Exception("No network")
        pc = PricingClient(session)
        price = pc.ec2_hourly("m5.large", "us-east-1")
        assert price == pytest.approx(0.096, rel=0.01)

    def test_ec2_fallback_for_unknown_type(self):
        session = MagicMock()
        session.client.side_effect = Exception("No network")
        pc = PricingClient(session)
        price = pc.ec2_hourly("x9.superlarge", "us-east-1")
        assert price > 0

    def test_rds_fallback(self):
        session = MagicMock()
        session.client.side_effect = Exception("No network")
        pc = PricingClient(session)
        price = pc.rds_hourly("db.m5.large", "MySQL", "us-east-1")
        assert price == pytest.approx(0.171, rel=0.01)

    def test_eip_monthly_fixed(self):
        session = MagicMock()
        pc = PricingClient(session)
        assert pc.eip_monthly() > 3.0

    def test_snapshot_per_gb_fixed(self):
        session = MagicMock()
        pc = PricingClient(session)
        assert pc.snapshot_per_gb() == pytest.approx(0.05)


class TestPricingClientLiveAPI:
    def test_ec2_live_price_used_when_available(self):
        """When the API returns a price, it should be used instead of fallback."""
        mock_client = MagicMock()
        mock_client.get_products.return_value = _make_price_response("0.1234")

        session = MagicMock()
        session.client.return_value = mock_client

        pc = PricingClient(session)
        price = pc.ec2_hourly("m5.large", "us-east-1")
        assert price == pytest.approx(0.1234)

    def test_cache_prevents_duplicate_api_calls(self):
        """The same price should not trigger a second API call."""
        mock_client = MagicMock()
        mock_client.get_products.return_value = _make_price_response("0.096")

        session = MagicMock()
        session.client.return_value = mock_client

        pc = PricingClient(session)
        pc.ec2_hourly("m5.large", "us-east-1")
        pc.ec2_hourly("m5.large", "us-east-1")  # second call

        assert mock_client.get_products.call_count == 1

    def test_empty_price_list_falls_back(self):
        """An empty PriceList response should trigger fallback."""
        mock_client = MagicMock()
        mock_client.get_products.return_value = {"PriceList": []}

        session = MagicMock()
        session.client.return_value = mock_client

        pc = PricingClient(session)
        price = pc.ec2_hourly("t3.micro", "us-east-1")
        assert price == pytest.approx(0.0104, rel=0.01)


class TestPricingClientSingleton:
    def test_singleton_returns_same_instance(self):
        session = MagicMock()
        session.client.side_effect = Exception("No network")
        pc1 = get_pricing_client(session)
        pc2 = get_pricing_client(session)
        assert pc1 is pc2

    def test_reset_clears_singleton(self):
        session = MagicMock()
        session.client.side_effect = Exception("No network")
        pc1 = get_pricing_client(session)
        reset_pricing_client()
        pc2 = get_pricing_client(session)
        assert pc1 is not pc2
