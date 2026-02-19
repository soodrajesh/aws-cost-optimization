"""
Live AWS Pricing module.

Fetches current on-demand prices from the AWS Price List Query API with
in-memory caching and fallback to hardcoded defaults.

Usage:
    from utils.pricing import get_pricing_client
    pc = get_pricing_client(session)
    hourly = pc.ec2_hourly(instance_type="m5.large", region="us-east-1")
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)

# The Pricing API is only available in us-east-1 (and ap-south-1, eu-central-1).
_PRICING_REGION = "us-east-1"

# Maximum concurrent Pricing API calls (the API has undocumented rate limits).
_API_SEMAPHORE = threading.Semaphore(5)

# Cache TTL: 24 hours (one scan run typically takes minutes).
_CACHE_TTL_SECONDS = 86400

# ---------------------------------------------------------------------------
# Region code -> location name mapping (used by Pricing API filters)
# ---------------------------------------------------------------------------
REGION_LOCATION_MAP: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "af-south-1": "Africa (Cape Town)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)",
    "ap-southeast-4": "Asia Pacific (Melbourne)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ca-central-1": "Canada (Central)",
    "ca-west-1": "Canada West (Calgary)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-central-2": "EU (Zurich)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-south-1": "EU (Milan)",
    "eu-south-2": "EU (Spain)",
    "eu-north-1": "EU (Stockholm)",
    "il-central-1": "Israel (Tel Aviv)",
    "me-south-1": "Middle East (Bahrain)",
    "me-central-1": "Middle East (UAE)",
    "sa-east-1": "South America (Sao Paulo)",
}

# ---------------------------------------------------------------------------
# Fallback / hardcoded pricing (us-east-1 on-demand, early 2026)
# These are used when the Pricing API is unavailable.
# ---------------------------------------------------------------------------
_FALLBACK_EC2_HOURLY: dict[str, float] = {
    "t2.micro": 0.0116, "t2.small": 0.023, "t2.medium": 0.0464,
    "t2.large": 0.0928, "t2.xlarge": 0.1856, "t2.2xlarge": 0.3712,
    "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416,
    "t3.large": 0.0832, "t3.xlarge": 0.1664, "t3.2xlarge": 0.3328,
    "t3a.micro": 0.0094, "t3a.small": 0.0188, "t3a.medium": 0.0376,
    "t3a.large": 0.0752,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384,
    "m5.4xlarge": 0.768, "m5.8xlarge": 1.536, "m5.12xlarge": 2.304,
    "m5.16xlarge": 3.072, "m5.24xlarge": 4.608,
    "m6i.large": 0.096, "m6i.xlarge": 0.192, "m6i.2xlarge": 0.384,
    "m6i.4xlarge": 0.768,
    "c5.large": 0.085, "c5.xlarge": 0.17, "c5.2xlarge": 0.34,
    "c5.4xlarge": 0.68,
    "c6i.large": 0.085, "c6i.xlarge": 0.17,
    "r5.large": 0.126, "r5.xlarge": 0.252, "r5.2xlarge": 0.504,
    "r5.4xlarge": 1.008,
    "r6i.large": 0.126, "r6i.xlarge": 0.252,
    "p3.2xlarge": 3.06, "p3.8xlarge": 12.24,
    "g4dn.xlarge": 0.526, "g4dn.2xlarge": 0.752,
}
_FALLBACK_EC2_DEFAULT = 0.10

_FALLBACK_RDS_HOURLY: dict[str, float] = {
    "db.t3.micro": 0.017, "db.t3.small": 0.034, "db.t3.medium": 0.068,
    "db.t3.large": 0.136, "db.t3.xlarge": 0.272, "db.t3.2xlarge": 0.544,
    "db.t4g.micro": 0.016, "db.t4g.small": 0.032, "db.t4g.medium": 0.065,
    "db.m5.large": 0.171, "db.m5.xlarge": 0.342, "db.m5.2xlarge": 0.684,
    "db.m5.4xlarge": 1.368,
    "db.m6g.large": 0.162, "db.m6g.xlarge": 0.325,
    "db.r5.large": 0.24, "db.r5.xlarge": 0.48, "db.r5.2xlarge": 0.96,
    "db.r6g.large": 0.228, "db.r6g.xlarge": 0.456,
}
_FALLBACK_RDS_DEFAULT = 0.17

_FALLBACK_EBS_PRICES: dict[str, float] = {
    "gp2": 0.10, "gp3": 0.08, "io1": 0.125, "io2": 0.125,
    "st1": 0.045, "sc1": 0.025, "standard": 0.05,
}

_FALLBACK_ELB_HOURLY: dict[str, float] = {
    "application": 0.0225, "network": 0.0225,
    "gateway": 0.0125, "classic": 0.025,
}

_FALLBACK_ELASTICACHE_HOURLY: dict[str, float] = {
    "cache.t3.micro": 0.017, "cache.t3.small": 0.034,
    "cache.t3.medium": 0.068,
    "cache.m6g.large": 0.149, "cache.m6g.xlarge": 0.298,
    "cache.r6g.large": 0.206, "cache.r6g.xlarge": 0.412,
    "cache.r7g.large": 0.175, "cache.r7g.xlarge": 0.350,
}
_FALLBACK_ELASTICACHE_DEFAULT = 0.15

# NAT Gateway: $0.045/hr + $0.045/GB processed
_FALLBACK_NAT_GW_HOURLY = 0.045
_FALLBACK_NAT_GW_PER_GB = 0.045

# Fargate: per vCPU-hr and per GB-hr (Linux/x86)
_FALLBACK_FARGATE_VCPU_HR = 0.04048
_FALLBACK_FARGATE_GB_HR = 0.004445

# DynamoDB on-demand (post Nov 2024 50% reduction)
_FALLBACK_DYNAMODB_WRU_PER_MILLION = 0.625
_FALLBACK_DYNAMODB_RRU_PER_MILLION = 0.125

# Data Transfer
_FALLBACK_DATA_TRANSFER_PER_GB = 0.09  # first 10TB tier, internet egress


# ---------------------------------------------------------------------------
# PricingClient
# ---------------------------------------------------------------------------
class PricingClient:
    """
    Fetches live on-demand pricing from the AWS Price List Query API.

    Uses an in-memory TTL cache and falls back to hardcoded prices when the
    API is unavailable or returns no results.
    """

    def __init__(self, session: boto3.Session) -> None:
        self._session = session
        self._client: Optional[Any] = None
        self._cache: dict[str, tuple[float, float]] = {}  # key -> (price, timestamp)
        self._api_available = True
        self._lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                self._client = self._session.client(
                    "pricing", region_name=_PRICING_REGION
                )
            except Exception as exc:
                logger.warning("Could not create Pricing API client: %s", exc)
                self._api_available = False
        return self._client

    def _cache_key(self, service_code: str, filters: list[dict], region: str) -> str:
        raw = f"{service_code}:{region}:{json.dumps(filters, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[float]:
        if key in self._cache:
            price, ts = self._cache[key]
            if time.time() - ts < _CACHE_TTL_SECONDS:
                return price
            del self._cache[key]
        return None

    def _set_cache(self, key: str, price: float) -> None:
        self._cache[key] = (price, time.time())

    def _query_price(
        self, service_code: str, filters: list[dict]
    ) -> Optional[float]:
        """
        Call get_products() with the given filters and extract the on-demand
        price from the first matching product.
        """
        if not self._api_available:
            return None

        client = self._get_client()
        if client is None:
            return None

        with _API_SEMAPHORE:
            try:
                response = client.get_products(
                    ServiceCode=service_code,
                    Filters=filters,
                    MaxResults=1,
                    FormatVersion="aws_v1",
                )
            except Exception as exc:
                logger.debug("Pricing API call failed for %s: %s", service_code, exc)
                return None

        price_list = response.get("PriceList", [])
        if not price_list:
            return None

        try:
            product = json.loads(price_list[0])
            terms = product.get("terms", {}).get("OnDemand", {})
            for offer_term in terms.values():
                for dimension in offer_term.get("priceDimensions", {}).values():
                    usd = dimension.get("pricePerUnit", {}).get("USD")
                    if usd:
                        price = float(usd)
                        if price > 0:
                            return price
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.debug("Failed to parse pricing response: %s", exc)

        return None

    def _build_filters(
        self,
        region: str,
        extra_filters: dict[str, str],
    ) -> list[dict]:
        location = REGION_LOCATION_MAP.get(region, "US East (N. Virginia)")
        filters = [{"Type": "TERM_MATCH", "Field": "location", "Value": location}]
        for field, value in extra_filters.items():
            filters.append({"Type": "TERM_MATCH", "Field": field, "Value": value})
        return filters

    # -------------------------------------------------------------------
    # Public helpers for each service
    # -------------------------------------------------------------------

    def ec2_hourly(
        self, instance_type: str, region: str = "us-east-1"
    ) -> float:
        filters = self._build_filters(region, {
            "instanceType": instance_type,
            "operatingSystem": "Linux",
            "tenancy": "Shared",
            "preInstalledSw": "NA",
            "capacitystatus": "Used",
        })
        key = self._cache_key("AmazonEC2", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonEC2", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        fallback = _FALLBACK_EC2_HOURLY.get(instance_type, _FALLBACK_EC2_DEFAULT)
        logger.debug("Using fallback EC2 price for %s: $%.4f/hr", instance_type, fallback)
        return fallback

    def rds_hourly(
        self, db_class: str, engine: str = "MySQL", region: str = "us-east-1"
    ) -> float:
        filters = self._build_filters(region, {
            "instanceType": db_class,
            "databaseEngine": engine,
            "deploymentOption": "Single-AZ",
        })
        key = self._cache_key("AmazonRDS", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonRDS", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        fallback = _FALLBACK_RDS_HOURLY.get(db_class, _FALLBACK_RDS_DEFAULT)
        logger.debug("Using fallback RDS price for %s: $%.4f/hr", db_class, fallback)
        return fallback

    def ebs_per_gb(
        self, volume_type: str = "gp2", region: str = "us-east-1"
    ) -> float:
        usage_map = {
            "gp2": "EBS:VolumeUsage.gp2",
            "gp3": "EBS:VolumeUsage.gp3",
            "io1": "EBS:VolumeUsage.piops",
            "io2": "EBS:VolumeUsage.io2",
            "st1": "EBS:VolumeUsage.st1",
            "sc1": "EBS:VolumeUsage.sc1",
            "standard": "EBS:VolumeUsage",
        }
        usage_type = usage_map.get(volume_type, "EBS:VolumeUsage.gp2")
        filters = self._build_filters(region, {
            "productFamily": "Storage",
            "usagetype": usage_type,
        })
        key = self._cache_key("AmazonEC2", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonEC2", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        return _FALLBACK_EBS_PRICES.get(volume_type, 0.10)

    def elb_hourly(
        self, lb_type: str = "application", region: str = "us-east-1"
    ) -> float:
        usage_map = {
            "application": "LoadBalancerUsage",
            "network": "LoadBalancerUsage",
            "gateway": "LoadBalancerUsage",
            "classic": "LoadBalancerUsage",
        }
        filters = self._build_filters(region, {
            "productFamily": "Load Balancer",
            "usagetype": usage_map.get(lb_type, "LoadBalancerUsage"),
        })
        key = self._cache_key("AWSELB", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AWSELB", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        return _FALLBACK_ELB_HOURLY.get(lb_type, 0.0225)

    def elasticache_hourly(
        self, node_type: str, region: str = "us-east-1"
    ) -> float:
        filters = self._build_filters(region, {
            "instanceType": node_type,
            "cacheEngine": "Redis",
        })
        key = self._cache_key("AmazonElastiCache", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonElastiCache", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        return _FALLBACK_ELASTICACHE_HOURLY.get(
            node_type, _FALLBACK_ELASTICACHE_DEFAULT
        )

    def nat_gateway_hourly(self, region: str = "us-east-1") -> float:
        filters = self._build_filters(region, {
            "productFamily": "NAT Gateway",
            "usagetype": "NatGateway-Hours",
        })
        key = self._cache_key("AmazonEC2", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonEC2", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        return _FALLBACK_NAT_GW_HOURLY

    def nat_gateway_per_gb(self, region: str = "us-east-1") -> float:
        filters = self._build_filters(region, {
            "productFamily": "NAT Gateway",
            "usagetype": "NatGateway-Bytes",
        })
        key = self._cache_key("AmazonEC2", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonEC2", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        return _FALLBACK_NAT_GW_PER_GB

    def fargate_vcpu_hr(self, region: str = "us-east-1") -> float:
        filters = self._build_filters(region, {
            "productFamily": "Compute",
            "usagetype": "USE1-Fargate-vCPU-Hours:perCPU",
        })
        key = self._cache_key("AmazonECS", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonECS", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        return _FALLBACK_FARGATE_VCPU_HR

    def fargate_gb_hr(self, region: str = "us-east-1") -> float:
        filters = self._build_filters(region, {
            "productFamily": "Compute",
            "usagetype": "USE1-Fargate-GB-Hours:perGB",
        })
        key = self._cache_key("AmazonECS", filters, region)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        price = self._query_price("AmazonECS", filters)
        if price is not None:
            self._set_cache(key, price)
            return price

        return _FALLBACK_FARGATE_GB_HR

    def snapshot_per_gb(self, region: str = "us-east-1") -> float:
        """EBS snapshot storage: ~$0.05/GB/month."""
        return 0.05

    def eip_monthly(self) -> float:
        """Unused Elastic IP: $0.005/hr = ~$3.65/month."""
        return 3.65

    def data_transfer_per_gb(self, region: str = "us-east-1") -> float:
        """Internet egress (first 10 TB tier)."""
        return _FALLBACK_DATA_TRANSFER_PER_GB

    def rds_snapshot_per_gb(self) -> float:
        """RDS manual snapshot storage: $0.095/GB/month."""
        return 0.095

    def cw_log_storage_per_gb(self) -> float:
        """CloudWatch Logs stored data: $0.03/GB/month."""
        return 0.03

    def cw_alarm_monthly(self) -> float:
        """CloudWatch standard alarm: $0.10/alarm/month."""
        return 0.10

    def s3_standard_per_gb(self) -> float:
        """S3 Standard storage: $0.023/GB/month."""
        return 0.023

    def s3_ia_per_gb(self) -> float:
        """S3 Infrequent Access storage: $0.0125/GB/month."""
        return 0.0125


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_instance: Optional[PricingClient] = None
_instance_lock = threading.Lock()


def get_pricing_client(session: boto3.Session) -> PricingClient:
    """Return a module-level singleton PricingClient."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = PricingClient(session)
    return _instance


def reset_pricing_client() -> None:
    """Reset the singleton (used in tests)."""
    global _instance
    _instance = None
