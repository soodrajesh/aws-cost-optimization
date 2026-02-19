"""
Abstract base class for all resource analysers.
"""

from __future__ import annotations

import logging
from abc import ABC

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from config import Config
from models import Finding

_RETRY_CONFIG = BotoConfig(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=30,
)

# Access-denied error codes returned by various AWS services.
_ACCESS_DENIED_CODES = frozenset({
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedAccess",
    "AuthorizationError",
    "Forbidden",
})


class BaseAnalyser(ABC):
    """
    All resource analysers inherit from this class.
    Each analyser is responsible for a single AWS service and returns
    a list of Findings across all provided regions.
    """

    SERVICE_NAME: str = ""

    def __init__(self, session: boto3.Session, config: Config) -> None:
        self.session = session
        self.config = config
        self.logger = logging.getLogger(f"analyser.{self.SERVICE_NAME.lower()}")

    def analyse(self, regions: list[str]) -> list[Finding]:
        """
        Run the analysis across the given regions and return all findings.
        Default implementation loops over regions and calls _safe_analyse_region();
        a single bad region does not abort the full scan.
        Override in subclasses for global services (e.g. S3, IAM) that do not
        scan per-region.
        """
        findings: list[Finding] = []
        for region in regions:
            findings.extend(self._safe_analyse_region(region))
        return findings

    def _client(self, service: str, region: str):
        """Create a boto3 client with production retry config."""
        return self.session.client(service, region_name=region, config=_RETRY_CONFIG)

    def _resource(self, service: str, region: str):
        """Create a boto3 resource with production retry config."""
        return self.session.resource(service, region_name=region, config=_RETRY_CONFIG)

    def _safe_analyse_region(self, region: str) -> list[Finding]:
        """
        Wrapper around per-region analysis that gracefully handles
        permission errors instead of crashing the entire scan.

        Subclasses that override analyse() with per-region logic should
        call this method in their region loop.
        """
        try:
            return self._analyse_region(region)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in _ACCESS_DENIED_CODES:
                self.logger.warning(
                    "Insufficient permissions for %s in %s — skipping",
                    self.SERVICE_NAME, region,
                )
                return []
            self.logger.error(
                "%s analyser failed in %s: %s", self.SERVICE_NAME, region, exc,
            )
            return []
        except (BotoCoreError, Exception) as exc:
            self.logger.error(
                "%s analyser failed in %s: %s", self.SERVICE_NAME, region, exc,
            )
            return []

    def _analyse_region(self, region: str) -> list[Finding]:
        """
        Override in subclass to implement per-region analysis.
        Called by _safe_analyse_region().
        """
        raise NotImplementedError
