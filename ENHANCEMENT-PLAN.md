# AWS Cost Optimization Tool - Production Enhancement Plan

## Context

The tool currently scans 7 fixed AWS services with hardcoded us-east-1 pricing and produces a functional but basic PDF report. The goal is to make it **production-grade** and **CTO-presentation ready** by:
- Using Cost Explorer data to dynamically discover what's billed, then analyzing those services
- Fetching live AWS pricing instead of hardcoded values
- Adding 5 commonly high-cost service analysers (NAT Gateway, DynamoDB, ElastiCache, ECS/Fargate, Data Transfer)
- Overhauling the recommendation engine with categorization (quick wins vs strategic), ROI calculations, and implementation steps
- Upgrading the PDF to executive quality with savings roadmap, priority matrix, coverage gap analysis, and narrative summaries

---

## Phase 1: Foundation Layer

### 1A. Live Pricing Module

**New file: `utils/pricing.py`** (~300 lines)

- `PricingClient` class using boto3 `pricing` client in us-east-1
- `get_price(service_code, filters, region)` with in-memory 24hr TTL cache
- Fallback chain: API call -> cache -> hardcoded values from current `cost_estimator.py`
- Region-code-to-location-name mapping (e.g., `us-east-1` -> `"US East (N. Virginia)"`)
- `threading.Semaphore(5)` to rate-limit concurrent Pricing API calls
- Requires IAM permission: `pricing:GetProducts`

**Modify: `utils/cost_estimator.py`**

- Refactor all `estimate_*()` functions to accept `region` parameter
- Call `PricingClient.get_price()` instead of hardcoded dicts
- Keep existing hardcoded dicts as fallback values
- Add new estimators: `estimate_nat_gateway()`, `estimate_dynamodb_table()`, `estimate_elasticache_node()`, `estimate_fargate_task()`, `estimate_data_transfer()`

### 1B. Cost Explorer-Driven Service Discovery

**New file: `utils/service_registry.py`** (~80 lines)

- Centralized `SERVICE_REGISTRY` dict mapping service keys to display names, CE aliases, analyser classes, and global/regional flag
- Replaces duplicated `_SERVICE_ALIASES` in `pdf_builder.py` (line 569) and implicit aliases in `main.py` (line 253)

**Modify: `analysers/cost_explorer.py`**

- Add `get_top_billed_services(min_monthly_spend=50)` method
- Returns sorted list of `{"service": str, "monthly_avg": float, "has_analyser": bool}`
- Cross-references against `SERVICE_REGISTRY` for coverage detection

**Modify: `models.py`**

- Add `finding_type: str = ""` to `Finding` (machine-readable, e.g. `"idle_instance"`, `"unattached_ebs"`)
- Add to `ScanResult`: `top_billed_services: list[dict]`, `uncovered_high_spend: list[dict]`
- Add to `Recommendation`: `category` (quick_win/strategic/long_term), `implementation_effort` (low/medium/high), `estimated_hours`, `risk_level`, `risk_notes`, `annualized_saving`, `roi_multiple`, `implementation_steps: list[str]`

**Modify: `config.py`**

- Expand `SUPPORTED_SERVICES` from 7 to 12 (add `nat_gateway`, `dynamodb`, `elasticache`, `ecs`, `data_transfer`)
- Add thresholds for new services
- Add `validate()` method for input validation

---

## Phase 2: New Service Analysers (5 files, can be done in parallel)

### 2A. NAT Gateway Analyser - `analysers/nat_gateway.py` (~180 lines)

NAT Gateways are often in the top 3 hidden AWS costs ($0.045/hr + $0.045/GB processed).

| Check | Metric | Severity | Action |
|-------|--------|----------|--------|
| Idle NAT Gateways (<1GB throughput/14d) | CloudWatch `BytesOutToDestination` | HIGH | Delete and review routing |
| High data-processing (>100 GB/mo) | CloudWatch `BytesOutToDestination` | MEDIUM | Recommend VPC endpoints for S3/DynamoDB |
| Multiple NAT GWs in same AZ | EC2 API subnet mapping | LOW | Consolidate |

API: `ec2.describe_nat_gateways()`, CloudWatch `AWS/NATGateway` namespace

### 2B. DynamoDB Analyser - `analysers/dynamodb.py` (~200 lines)

| Check | Metric | Severity | Action |
|-------|--------|----------|--------|
| Over-provisioned (consumed < 20% of provisioned) | CloudWatch `ConsumedRead/WriteCapacityUnits` | HIGH | Right-size or switch to on-demand |
| Unused tables (0 RCU+WCU over 14d) | CloudWatch | HIGH | Delete or archive |
| No auto-scaling on provisioned tables | `application-autoscaling.describe_scalable_targets()` | MEDIUM | Enable auto-scaling |
| On-demand vs provisioned cost comparison | Usage patterns | LOW | Switch billing mode if beneficial |

### 2C. ElastiCache Analyser - `analysers/elasticache.py` (~170 lines)

| Check | Metric | Severity | Action |
|-------|--------|----------|--------|
| Idle clusters (0 connections, 14d) | CloudWatch `CurrConnections` | HIGH | Delete cluster |
| Over-sized nodes (CPU <10%, memory <30%) | CloudWatch `EngineCPUUtilization`, `DatabaseMemoryUsagePercentage` | MEDIUM | Downsize node type |

### 2D. ECS/Fargate Analyser - `analysers/ecs.py` (~200 lines)

| Check | Metric | Severity | Action |
|-------|--------|----------|--------|
| Over-provisioned tasks (CPU+memory <20%) | CloudWatch `CPUUtilization`, `MemoryUtilization` | MEDIUM | Right-size task definitions |
| Idle services (RunningTaskCount=0, >7d) | CloudWatch `RunningTaskCount` | HIGH | Delete service |
| Fargate Spot opportunity (non-prod, on-demand) | Service tags/names | LOW | Switch to Fargate Spot (up to 70% savings) |

### 2E. Data Transfer Analyser - `analysers/data_transfer.py` (~150 lines)

Uses Cost Explorer data exclusively (no resource enumeration):

| Check | Source | Severity | Action |
|-------|--------|----------|--------|
| High inter-region transfer (>$100/mo) | CE USAGE_TYPE grouping | MEDIUM | Co-locate resources |
| High internet egress | CE USAGE_TYPE grouping | MEDIUM | Use CloudFront, VPC endpoints |
| Cross-AZ transfer costs | CE USAGE_TYPE grouping | LOW | Review architecture for same-AZ placement |

---

## Phase 3: Recommendation Engine Overhaul

**Modify: `main.py` - `build_recommendations()` (lines 119-276)**

### Categorization

| Category | Criteria | Examples |
|----------|----------|----------|
| **Quick Win** | effort=low, risk=low, saving>0 | Delete EBS volumes, release EIPs, set log retention, delete old snapshots |
| **Strategic** | effort=medium, saving>$100/mo | Right-size EC2/RDS, Savings Plans, S3 lifecycle, VPC endpoints |
| **Long-term** | effort=high, architectural change | Migrate billing modes, Fargate Spot, major re-architecture |

### Effort & Risk Mapping

Centralized `_EFFORT_MAP` dict keyed by finding_type with effort, hours, risk_level, risk_notes.

### ROI Calculation

```
annualized_saving = monthly_saving * 12
implementation_cost = estimated_hours * $150/hr (configurable)
roi_multiple = annualized_saving / implementation_cost
```

### Implementation Steps

Each recommendation template includes 3-5 concrete action items (e.g., "Verify no dependencies -> Create snapshot backup -> Terminate instance -> Verify in Cost Explorer").

---

## Phase 4: CTO-Level PDF Report Enhancements

**Modify: `report/pdf_builder.py`** (add ~250 lines)
**Modify: `report/charts.py`** (add ~150 lines)

### New/Enhanced Sections (in order of appearance)

1. **Enhanced Executive Summary** - Add dynamic narrative paragraph (3-4 sentences telling the story), quick wins callout, current vs optimized monthly spend

2. **NEW: Savings Roadmap** (after exec summary)
   - Priority matrix table: Quick Wins | Strategic | Long-term with savings & effort
   - ROI summary table: Monthly/Annual savings, effort hours, ROI multiple per category
   - Cumulative savings projection chart (12-month view, phased implementation)

3. **Spend Trends** - Existing (keep as-is)

4. **Service Cost Breakdown** - Existing (keep as-is)

5. **NEW: Coverage Gap Analysis** (after service breakdown)
   - Table of high-spend services without detailed analysis
   - Monthly average spend, trend, and recommendation to investigate

6. **Enhanced Findings & Recommendations** (per-service sections)
   - Risk assessment callout boxes (yellow/red for medium/high risk)
   - Implementation steps (numbered list under each recommendation)
   - Category badge (Quick Win / Strategic / Long-term)

7. **NEW: Commitment-Based Savings Analysis** (before appendix)
   - Services with stable high spend (CV < 0.3, >$500/mo) as RI/SP candidates
   - Conservative 30% savings estimate
   - Compute Savings Plans vs EC2 Instance Savings Plans vs RDS RI breakdown

8. **Appendix** - Existing (keep as-is)

### New Charts

| Chart | Type | Purpose |
|-------|------|---------|
| `current_vs_optimized_bar()` | Waterfall/horizontal bar | Show current spend -> savings -> optimized spend |
| `savings_roadmap_projection()` | Stacked area (12-month) | Project cumulative savings as phases complete |
| `priority_matrix_quadrant()` | Scatter plot | Effort (x) vs Savings (y), sized by findings count |
| `coverage_heatmap()` | Horizontal bar | Top 15 billed services, green=covered, red=gap |

---

## Phase 5: Production Hardening

### 5A. Retry & Throttle Handling
**Modify: `aws_client.py`** - Add `botocore.config.Config(retries={"max_attempts": 5, "mode": "adaptive"})` to all client creation. This handles throttling automatically with exponential backoff.

### 5B. Permission-Aware Error Handling
**New: `utils/exceptions.py`** (~30 lines) - `CostToolError`, `AnalyserError`, `PricingError`
**Modify: `analysers/base.py`** - Add `_safe_analyse_region()` that catches `AccessDenied`/`UnauthorizedAccess` and logs warning instead of crashing

### 5C. JSON Export
**New: `report/json_exporter.py`** (~80 lines) - Structured JSON export using `dataclasses.asdict()`
**Modify: `main.py`** - Add `--format pdf json` CLI flag

### 5D. Logging
**Modify: `main.py`** - Add file handler (`aws-cost-scan-{date}.log`), add `--quiet` flag

---

## Phase 6: Tests

**New: `tests/` directory** (~600 lines total)
- `conftest.py` - Shared fixtures (mock sessions, sample findings/trends)
- `test_cost_estimator.py` - Unit tests for all estimate functions
- `test_pricing.py` - Mock Pricing API responses
- `test_models.py` - ScanResult properties, Finding post_init
- `test_recommendation_engine.py` - build_recommendations with mock data
- `test_pdf_builder.py` - Smoke test (PDF generation doesn't crash)

Add to `requirements.txt`: `pytest>=8.0.0`, `moto>=5.0.0`

---

## Implementation Order & Dependencies

```
Phase 1A: Live Pricing Module          ──┐
Phase 1B: CE Service Discovery + Models ──┤
Phase 5A: Retry/Throttle Handling       ──┤
                                          ├──> Phase 2: New Analysers (5 in parallel)
                                          │         │
                                          │         v
                                          ├──> Phase 3: Recommendation Engine
                                          │         │
                                          │         v
                                          └──> Phase 4: PDF Report Enhancements
                                                    │
                                                    v
                                              Phase 5B-D: Hardening + Export
                                                    │
                                                    v
                                              Phase 6: Tests
```

## Files Summary

| File | Action | Est. Lines |
|------|--------|-----------|
| `utils/pricing.py` | NEW | ~300 |
| `utils/service_registry.py` | NEW | ~80 |
| `utils/exceptions.py` | NEW | ~30 |
| `utils/cost_estimator.py` | MODIFY | +100 |
| `analysers/nat_gateway.py` | NEW | ~180 |
| `analysers/dynamodb.py` | NEW | ~200 |
| `analysers/elasticache.py` | NEW | ~170 |
| `analysers/ecs.py` | NEW | ~200 |
| `analysers/data_transfer.py` | NEW | ~150 |
| `models.py` | MODIFY | +60 |
| `config.py` | MODIFY | +50 |
| `main.py` | MODIFY | +120 |
| `analysers/base.py` | MODIFY | +25 |
| `analysers/cost_explorer.py` | MODIFY | +60 |
| `aws_client.py` | MODIFY | +40 |
| `report/pdf_builder.py` | MODIFY | +250 |
| `report/charts.py` | MODIFY | +150 |
| `report/json_exporter.py` | NEW | ~80 |
| `tests/*` | NEW | ~600 |
| **Total new/modified** | | **~2,800 lines** |

## Verification

After implementation, verify end-to-end by:
1. Run `python main.py --profile <test-profile> --debug` against a real AWS account
2. Confirm Cost Explorer data drives service discovery (check console output for coverage gaps)
3. Verify live pricing lookups succeed (check debug logs for pricing API calls vs fallbacks)
4. Open PDF and review all new sections: executive narrative, savings roadmap, coverage gaps, priority matrix
5. Verify new analysers (NAT GW, DynamoDB, ElastiCache, ECS, Data Transfer) produce findings
6. Test graceful degradation: run with a profile that lacks `pricing:GetProducts` permission - should fall back to hardcoded prices with a warning
7. Test `--format json` export
8. Run `pytest tests/` - all tests should pass
