"""
Custom exception hierarchy for the AWS Cost Optimisation tool.
"""

from __future__ import annotations


class CostToolError(Exception):
    """Base exception for the cost optimisation tool."""


class AnalyserError(CostToolError):
    """Raised when an analyser encounters an unrecoverable error."""

    def __init__(self, service: str, region: str, message: str) -> None:
        self.service = service
        self.region = region
        super().__init__(f"[{service}/{region}] {message}")


class PricingError(CostToolError):
    """Raised when pricing lookup fails completely (including fallback)."""


class PermissionDeniedError(CostToolError):
    """Raised when AWS permissions are insufficient for a service/region."""

    def __init__(self, service: str, region: str, message: str = "") -> None:
        self.service = service
        self.region = region
        super().__init__(
            f"Insufficient permissions for {service} in {region}"
            + (f": {message}" if message else "")
        )
