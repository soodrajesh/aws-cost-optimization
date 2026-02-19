"""
Lambda analyser.

Checks for:
- Functions with zero invocations (unused)           → delete
- Over-provisioned memory (usage << allocation)      → right-size memory
- ARM/Graviton migration (x86 runtimes)              → arm64 architecture (~20% cheaper)
- Deprecated runtimes                                → upgrade runtime (AWS will block updates)
- Excessive timeout (timeout >> avg duration)        → reduce timeout, save on runaway invocations
- High error rate (≥ 5% of invocations fail)         → fix errors to stop paying for retried compute
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from aws_client import paginate, safe_call
from models import Finding, Severity
from utils.cost_estimator import estimate_lambda_idle

logger = logging.getLogger(__name__)

# Runtimes that support arm64/Graviton (can be migrated)
_ARM64_COMPATIBLE_RUNTIMES = {
    "python3.8", "python3.9", "python3.10", "python3.11", "python3.12",
    "nodejs16.x", "nodejs18.x", "nodejs20.x",
    "java11", "java17", "java21",
    "dotnet6", "dotnet8",
}

# Runtimes that are deprecated or end-of-life — flag for upgrade
_DEPRECATED_RUNTIMES = {
    "python2.7", "python3.6", "python3.7",
    "nodejs10.x", "nodejs12.x", "nodejs14.x",
    "java8", "java8.al2",
    "ruby2.5", "ruby2.7",
    "dotnetcore2.1", "dotnetcore3.1", "dotnet5.0",
    "go1.x",
}

# arm64 price is ~20% lower than x86_64
_GRAVITON_SAVING_PCT = 0.20


class LambdaAnalyser(BaseAnalyser):
    SERVICE_NAME = "Lambda"

    def _analyse_region(self, region: str) -> list[Finding]:
        findings: list[Finding] = []
        lam = self._client("lambda", region)
        cw = self._client("cloudwatch", region)

        functions = paginate(lam, "list_functions", "Functions")

        for fn in functions:
            fn_name = fn["FunctionName"]
            fn_arn = fn["FunctionArn"]
            memory_mb = fn.get("MemorySize", 128)
            runtime = fn.get("Runtime", "unknown")
            architecture = fn.get("Architectures", ["x86_64"])[0]

            invocations = self._get_invocation_count(cw, fn_name)

            if invocations == 0:
                findings.append(Finding(
                    service="Lambda",
                    region=region,
                    resource_id=fn_arn,
                    resource_name=fn_name,
                    issue=f"No invocations in the last {self.config.lambda_idle_days} days — candidate for deletion",
                    estimated_monthly_saving_usd=estimate_lambda_idle(memory_mb),
                    severity=Severity.MEDIUM,
                    finding_type="idle_lambda",
                    details={
                        "memory_mb": memory_mb,
                        "runtime": runtime,
                        "last_modified": fn.get("LastModified", ""),
                    },
                ))
                # Still check for deprecated runtime even on idle functions
                if runtime in _DEPRECATED_RUNTIMES:
                    findings.append(Finding(
                        service="Lambda",
                        region=region,
                        resource_id=fn_arn,
                        resource_name=fn_name,
                        issue=(
                            f"Deprecated runtime '{runtime}' — AWS will block updates; "
                            f"migrate to a supported runtime to avoid disruption"
                        ),
                        estimated_monthly_saving_usd=0.0,
                        severity=Severity.HIGH,
                        finding_type="lambda_deprecated_runtime",
                        details={"runtime": runtime, "architecture": architecture},
                    ))
                continue

            # Check memory over-provisioning
            max_memory_used = self._get_max_memory_used(cw, fn_name)
            if max_memory_used is not None and memory_mb > 0:
                utilisation_pct = (max_memory_used / memory_mb) * 100
                if utilisation_pct < self.config.lambda_memory_utilisation_pct:
                    suggested_mb = self._suggest_memory(max_memory_used)
                    saving = estimate_lambda_idle(memory_mb - suggested_mb)
                    findings.append(Finding(
                        service="Lambda",
                        region=region,
                        resource_id=fn_arn,
                        resource_name=fn_name,
                        issue=(
                            f"Over-provisioned memory: using {max_memory_used:.0f} MB "
                            f"of {memory_mb} MB ({utilisation_pct:.1f}% utilisation) — "
                            f"reduce to {suggested_mb} MB"
                        ),
                        estimated_monthly_saving_usd=saving,
                        severity=Severity.LOW,
                        finding_type="lambda_memory_rightsizing",
                        details={
                            "memory_mb": memory_mb,
                            "max_memory_used_mb": round(max_memory_used, 1),
                            "utilisation_pct": round(utilisation_pct, 1),
                            "suggested_memory_mb": suggested_mb,
                            "runtime": runtime,
                        },
                    ))

            # Graviton/ARM64 migration opportunity
            if architecture == "x86_64" and runtime in _ARM64_COMPATIBLE_RUNTIMES:
                # Estimate monthly Lambda cost (compute + request cost approximation)
                monthly_compute_cost = self._estimate_monthly_cost(cw, fn_name, memory_mb)
                if monthly_compute_cost > 0:
                    saving = round(monthly_compute_cost * _GRAVITON_SAVING_PCT, 4)
                    if saving > 0.10:
                        findings.append(Finding(
                            service="Lambda",
                            region=region,
                            resource_id=fn_arn,
                            resource_name=fn_name,
                            issue=(
                                f"Graviton migration: switch to arm64 architecture — "
                                f"~20% lower compute cost with equal or better performance "
                                f"for {runtime} runtime"
                            ),
                            estimated_monthly_saving_usd=saving,
                            severity=Severity.LOW,
                            finding_type="lambda_graviton",
                            details={
                                "current_architecture": "x86_64",
                                "suggested_architecture": "arm64",
                                "runtime": runtime,
                                "saving_pct": 20,
                            },
                        ))

            # Deprecated runtime
            if runtime in _DEPRECATED_RUNTIMES:
                findings.append(Finding(
                    service="Lambda",
                    region=region,
                    resource_id=fn_arn,
                    resource_name=fn_name,
                    issue=(
                        f"Deprecated runtime '{runtime}' — AWS will block code updates on this function; "
                        f"migrate to a supported runtime immediately"
                    ),
                    estimated_monthly_saving_usd=0.0,
                    severity=Severity.HIGH,
                    finding_type="lambda_deprecated_runtime",
                    details={"runtime": runtime, "architecture": architecture},
                ))

            # Excessive timeout: configured timeout is ≥ 5x the average execution duration
            configured_timeout_s = fn.get("Timeout", 3)
            avg_duration_ms = self._get_avg_duration(cw, fn_name)
            if avg_duration_ms is not None and avg_duration_ms > 0:
                avg_duration_s = avg_duration_ms / 1000
                # If timeout is ≥ 5x average, a single bug-triggered runaway invocation
                # can rack up 5x the normal cost before being killed
                if configured_timeout_s >= max(avg_duration_s * 5, 10):
                    suggested_timeout = max(int(avg_duration_s * 3), 1)
                    # Saving: on SQS/async triggers, Lambda retries up to 3x; each runaway
                    # invocation consumes (timeout - avg_duration) extra seconds of GB-seconds
                    extra_gb_seconds_per_runaway = (configured_timeout_s - avg_duration_s) * (memory_mb / 1024)
                    # Assume 0.1% of invocations are runaway (conservative)
                    monthly_extra_cost = extra_gb_seconds_per_runaway * invocations * 0.001 * 0.0000166667
                    if monthly_extra_cost > 0.01 or configured_timeout_s > 60:
                        findings.append(Finding(
                            service="Lambda",
                            region=region,
                            resource_id=fn_arn,
                            resource_name=fn_name,
                            issue=(
                                f"Excessive timeout: configured {configured_timeout_s}s, "
                                f"avg duration {avg_duration_s:.1f}s — "
                                f"reduce to ~{suggested_timeout}s to limit blast radius of runaway invocations"
                            ),
                            estimated_monthly_saving_usd=round(monthly_extra_cost, 4),
                            severity=Severity.LOW,
                            finding_type="lambda_excessive_timeout",
                            details={
                                "configured_timeout_s": configured_timeout_s,
                                "avg_duration_s": round(avg_duration_s, 2),
                                "suggested_timeout_s": suggested_timeout,
                                "memory_mb": memory_mb,
                            },
                        ))

            # High error rate: ≥ 5% of invocations result in errors
            error_rate_pct = self._get_error_rate(cw, fn_name)
            if error_rate_pct is not None and error_rate_pct >= 5.0:
                # Each failed invocation is billed (Lambda doesn't refund errors)
                # For async/SQS, failed invocations are automatically retried (2x), tripling cost
                wasted_cost = self._estimate_monthly_cost(cw, fn_name, memory_mb) * (error_rate_pct / 100)
                findings.append(Finding(
                    service="Lambda",
                    region=region,
                    resource_id=fn_arn,
                    resource_name=fn_name,
                    issue=(
                        f"High error rate: {error_rate_pct:.1f}% of invocations are failing — "
                        f"all failed invocations are billed; async/SQS triggers retry 2-3x, "
                        f"multiplying wasted compute cost"
                    ),
                    estimated_monthly_saving_usd=round(wasted_cost, 4),
                    severity=Severity.HIGH if error_rate_pct >= 20 else Severity.MEDIUM,
                    finding_type="lambda_high_error_rate",
                    details={
                        "error_rate_pct": round(error_rate_pct, 1),
                        "runtime": runtime,
                        "memory_mb": memory_mb,
                    },
                ))

        return findings

    def _get_invocation_count(self, cw, fn_name: str) -> int:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.config.lambda_idle_days)
        response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/Lambda",
            MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=self.config.lambda_idle_days * 86400,
            Statistics=["Sum"],
        )
        if not response:
            return 0
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return 0
        return int(sum(dp["Sum"] for dp in datapoints))

    def _get_max_memory_used(self, cw, fn_name: str) -> float | None:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=30)
        response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/Lambda",
            MetricName="MaxMemoryUsed",
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=30 * 86400,
            Statistics=["Maximum"],
        )
        if not response:
            return None
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return None
        return max(dp["Maximum"] for dp in datapoints)

    def _get_avg_duration(self, cw, fn_name: str) -> float | None:
        """Return average invocation duration in milliseconds over the last 30 days."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=30)
        response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/Lambda",
            MetricName="Duration",
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=30 * 86400,
            Statistics=["Average"],
        )
        if not response:
            return None
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return None
        return datapoints[0]["Average"]

    def _get_error_rate(self, cw, fn_name: str) -> float | None:
        """Return percentage of invocations that resulted in errors over the last 30 days."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=30)
        period = 30 * 86400

        inv_response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/Lambda",
            MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Sum"],
        ) or {}

        err_response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/Lambda",
            MetricName="Errors",
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Sum"],
        ) or {}

        inv_dps = inv_response.get("Datapoints", [])
        err_dps = err_response.get("Datapoints", [])

        if not inv_dps:
            return None

        total_invocations = sum(dp["Sum"] for dp in inv_dps)
        if total_invocations == 0:
            return None

        total_errors = sum(dp["Sum"] for dp in err_dps) if err_dps else 0
        return round((total_errors / total_invocations) * 100, 2)

    def _estimate_monthly_cost(self, cw, fn_name: str, memory_mb: int) -> float:
        """Rough monthly Lambda compute cost estimate based on invocation count and avg duration."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=30)
        period = 30 * 86400

        inv_response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/Lambda",
            MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Sum"],
        ) or {}

        dur_response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/Lambda",
            MetricName="Duration",
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Average"],
        ) or {}

        inv_dps = inv_response.get("Datapoints", [])
        dur_dps = dur_response.get("Datapoints", [])

        if not inv_dps or not dur_dps:
            return 0.0

        invocations = sum(dp["Sum"] for dp in inv_dps)
        avg_duration_ms = dur_dps[0]["Average"]

        # Lambda pricing: $0.0000166667 per GB-second
        gb_seconds = (memory_mb / 1024) * (avg_duration_ms / 1000) * invocations
        return round(gb_seconds * 0.0000166667, 4)

    @staticmethod
    def _suggest_memory(max_used_mb: float) -> int:
        """Round up to the nearest Lambda memory size increment with 20% headroom."""
        target = max_used_mb * 1.2
        steps = [128, 192, 256, 320, 384, 448, 512, 576, 640, 704, 768, 832, 896, 960, 1024,
                 1088, 1152, 1216, 1280, 1344, 1408, 1472, 1536, 1600, 1664, 1728, 1792, 1856,
                 1920, 1984, 2048, 2112, 2176, 2240, 2304, 2368, 2432, 2496, 2560, 3008, 4096,
                 5120, 6144, 7168, 8192, 9216, 10240]
        for step in steps:
            if step >= target:
                return step
        return 10240
