"""
AWS Cost Explorer analyser.

Fetches 6 months of actual spend grouped by AWS service, computes
month-over-month trends, detects anomalies, and retrieves a 1-month
cost forecast.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, timedelta
from typing import Optional

import boto3

from config import Config
from models import CostForecast, CostTrend

logger = logging.getLogger(__name__)

# Cost Explorer API is only available in us-east-1
_CE_REGION = "us-east-1"


def _month_range(months: int) -> tuple[str, str]:
    """
    Return (start_date, end_date) strings in YYYY-MM-DD format covering
    the last `months` complete calendar months up to today.
    """
    today = date.today()
    # End at the start of the current month (CE end date is exclusive)
    end = date(today.year, today.month, 1)
    # Go back `months` months for the start
    year = end.year
    month = end.month - months
    while month <= 0:
        month += 12
        year -= 1
    start = date(year, month, 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _forecast_range() -> tuple[str, str]:
    """Return a date range covering the current (incomplete) month for forecasting."""
    today = date.today()
    start = today.strftime("%Y-%m-%d")
    # End of current month
    if today.month == 12:
        end = date(today.year + 1, 1, 1)
    else:
        end = date(today.year, today.month + 1, 1)
    return start, end.strftime("%Y-%m-%d")


class CostExplorerAnalyser:
    """Fetches and analyses AWS Cost Explorer data."""

    def __init__(self, session: boto3.Session, config: Config) -> None:
        self.session = session
        self.config = config
        self.client = session.client("ce", region_name=_CE_REGION)

    def get_trends(self) -> list[CostTrend]:
        """
        Retrieve monthly spend grouped by AWS service for the configured
        number of months and compute trend metrics.
        """
        start, end = _month_range(self.config.months)
        logger.info("Fetching Cost Explorer data from %s to %s", start, end)

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
        except Exception as exc:
            logger.error("Cost Explorer API call failed: %s", exc)
            return []

        # Collect per-service monthly spend
        # Structure: { service_name: { "YYYY-MM": cost_float } }
        service_data: dict[str, dict[str, float]] = {}

        for period in response.get("ResultsByTime", []):
            month_label = period["TimePeriod"]["Start"][:7]  # "YYYY-MM"
            for group in period.get("Groups", []):
                service_name = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                service_data.setdefault(service_name, {})[month_label] = amount

        trends: list[CostTrend] = []
        for service, monthly in service_data.items():
            # Skip services with negligible spend (< $0.01 total)
            total = sum(monthly.values())
            if total < 0.01:
                continue

            sorted_months = sorted(monthly.keys())
            costs = [monthly[m] for m in sorted_months]

            # Trend: % change from first to last month
            first, last = costs[0], costs[-1]
            if first > 0:
                trend_pct = ((last - first) / first) * 100
            else:
                trend_pct = 100.0 if last > 0 else 0.0

            # Anomaly detection: flag months where spend > mean + 2×stddev
            anomaly = False
            anomaly_months: list[str] = []
            if len(costs) >= 3:
                mean = statistics.mean(costs)
                try:
                    stdev = statistics.stdev(costs)
                except statistics.StatisticsError:
                    stdev = 0.0
                threshold = mean + 2 * stdev
                for month, cost in zip(sorted_months, costs):
                    if cost > threshold:
                        anomaly = True
                        anomaly_months.append(month)

            trends.append(
                CostTrend(
                    service=service,
                    monthly_costs=monthly,
                    total_spend=total,
                    trend_pct=trend_pct,
                    anomaly=anomaly,
                    anomaly_months=anomaly_months,
                )
            )

        # Sort by total spend descending
        trends.sort(key=lambda t: t.total_spend, reverse=True)
        logger.info("Collected cost trends for %d services", len(trends))
        return trends

    def get_top_billed_services(
        self,
        trends: list[CostTrend],
        min_monthly_spend: float = 50.0,
    ) -> list[dict]:
        """
        Analyse cost trends to identify the highest-spend services and
        flag which ones have a dedicated analyser.

        Returns a list of dicts sorted by monthly average spend (descending):
            {"service": str, "monthly_avg": float, "total_spend": float,
             "trend_pct": float, "has_analyser": bool, "service_key": str|None}
        """
        from utils.service_registry import ce_name_to_service_key

        result: list[dict] = []
        months = max(self.config.months, 1)

        for trend in trends:
            monthly_avg = trend.total_spend / months
            if monthly_avg < min_monthly_spend:
                continue

            service_key = ce_name_to_service_key(trend.service)
            result.append({
                "service": trend.service,
                "monthly_avg": round(monthly_avg, 2),
                "total_spend": round(trend.total_spend, 2),
                "trend_pct": round(trend.trend_pct, 1),
                "has_analyser": service_key is not None,
                "service_key": service_key,
            })

        result.sort(key=lambda x: x["monthly_avg"], reverse=True)
        return result

    def get_data_transfer_breakdown(self) -> list[dict]:
        """
        Fetch Cost Explorer data grouped by USAGE_TYPE for data transfer
        services. Returns a list of dicts with usage type and monthly cost.
        """
        start, end = _month_range(self.config.months)

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                Filter={
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["AWS Data Transfer"],
                    }
                },
                GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
            )
        except Exception as exc:
            logger.warning("Could not fetch data transfer breakdown: %s", exc)
            return []

        usage_data: dict[str, float] = {}
        for period in response.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                usage_type = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                usage_data[usage_type] = usage_data.get(usage_type, 0) + amount

        months = max(self.config.months, 1)
        result = []
        for usage_type, total in sorted(usage_data.items(), key=lambda x: -x[1]):
            if total < 1.0:
                continue
            result.append({
                "usage_type": usage_type,
                "total_spend": round(total, 2),
                "monthly_avg": round(total / months, 2),
            })

        return result

    def get_forecast(self) -> Optional[CostForecast]:
        """
        Fetch a cost forecast for the remainder of the current month.
        Returns None if the forecast API call fails (e.g. insufficient data).
        """
        start, end = _forecast_range()

        # CE requires start != end
        if start == end:
            return None

        try:
            response = self.client.get_cost_forecast(
                TimePeriod={"Start": start, "End": end},
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
            )
            total = response.get("Total", {})
            mean = float(total.get("Amount", 0))

            # Prediction intervals may not always be present
            intervals = response.get("ForecastResultsByTime", [])
            lower = upper = mean
            if intervals:
                lower = float(intervals[0].get("PredictionIntervalLowerBound", mean))
                upper = float(intervals[0].get("PredictionIntervalUpperBound", mean))

            return CostForecast(
                mean_usd=mean,
                lower_bound_usd=lower,
                upper_bound_usd=upper,
            )
        except Exception as exc:
            logger.warning("Could not fetch cost forecast (non-fatal): %s", exc)
            return None
