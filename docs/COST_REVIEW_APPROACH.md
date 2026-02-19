# AWS Pro Serv — Cost Optimisation Review Approach

This document describes how to use this tool and present findings as an **AWS Professional Services** cost optimisation engagement: scoping, execution, and customer-facing deliverables.

---

## 1. Engagement scope

- **Objective:** Identify and quantify cost waste across the customer’s AWS account and produce a prioritised savings roadmap with ROI.
- **In scope:** Resource-level waste (idle, over-provisioned, misconfigured), Cost Explorer trends, commitment-based opportunities (Savings Plans / RI), and coverage gaps for high-spend services.
- **Out of scope (tool):** Architecture redesign, TCO modelling for new workloads, and FinOps process setup — address these in workshops and follow-on engagements.

---

## 2. Pre-engagement

| Step | Action |
|------|--------|
| **Credentials** | Obtain a read-only AWS profile (or assume role) with the [required IAM permissions](../README.md#required-iam-permissions). Prefer a dedicated “cost-audit” role. |
| **Regions** | Confirm which regions are in scope (e.g. production only, or all enabled). Use `--regions` if the customer wants to limit the scan. |
| **Services** | Default is all analysers. Use `--services` to focus (e.g. `ec2 rds s3`) for a first pass or when permissions are partial. |
| **History** | Default is 6 months of Cost Explorer. Use `--months` to align with the customer’s billing cycle or compliance period. |

**Example (production-only, full scan, PDF + JSON):**

```bash
python main.py --profile customer-prod --regions us-east-1 eu-west-1 --output customer-cost-review-2026-02.pdf --format pdf json
```

---

## 3. Running the analysis

1. **Activate the environment** (e.g. `source .venv/bin/activate`).
2. **Run the tool** with the customer profile and options above.
3. **Capture outputs:** PDF report and, if requested, JSON for internal or custom analysis.
4. **Note:** Any permission warnings in the log (e.g. “Insufficient permissions for X in region Y”) — call these out in the review so the customer can expand access for a follow-up run if needed.

---

## 4. Interpreting the report (Pro Serv lens)

Use the generated PDF and JSON as the **technical evidence**; your role is to add context and prioritisation.

| Section | Use in customer discussion |
|--------|----------------------------|
| **Executive summary** | Lead with total potential monthly/annual savings and “current vs optimised” narrative. Tie to their stated goals (e.g. “reduce spend 15%”). |
| **Savings Roadmap / priority matrix** | Drive the conversation: Quick Wins first (low effort, low risk), then Strategic, then Long-term. Use ROI and risk callouts to agree order of implementation. |
| **Cost trends & forecast** | Explain whether spend is growing or stable; use anomalies to flag one-off spikes or recurring issues. Forecast sets expectation for “do nothing” run rate. |
| **Coverage gap analysis** | For high-spend services with no analyser, state that the tool did not perform resource-level checks; recommend CUR/QuickSight or a follow-on deep-dive. |
| **Per-service findings** | Walk through by service (e.g. EC2, RDS, S3). For each finding type, confirm with the customer: “Is this resource still needed?” and “What’s your change window?” |
| **Commitment-based savings** | Position Savings Plans / RI as a second phase after right-sizing and cleanup, so they don’t commit on wasteful usage. |

---

## 5. Prioritisation framework (customer-facing)

Present a simple 2×2 to agree what to do first:

- **High savings, low effort/risk** → Schedule in the next sprint (e.g. unused EIPs, old snapshots, S3 lifecycle, ECR lifecycle).
- **High savings, higher effort/risk** → Plan with change management and testing (e.g. right-sizing EC2/RDS, NAT Gateway consolidation).
- **Lower savings, low effort** → Batch into a “hygiene” backlog (e.g. log retention, stale alarms, IAM key rotation).
- **Long-term / architectural** → Roadmap item (e.g. VPC endpoints, data transfer optimisation, Graviton/Spot adoption).

Always tie back to **ROI** (annualised savings vs estimated implementation hours) so the customer can compare against their own labour cost.

---

## 6. Deliverables to the customer

1. **PDF report** — CTO/leadership-ready: cover page, executive summary, savings roadmap, trends, findings, and implementation steps.
2. **JSON export** (optional) — For their FinOps/engineering team to filter, dashboard, or integrate into ticketing.
3. **Summary slide or one-pager** — Top 5–10 recommendations with monthly savings, effort, and risk (you create this from the report).
4. **Verbal/written review** — Walk-through of the report, clarification of findings, and agreed priority order and next steps.

---

## 7. Post-review follow-up

- **Re-scan after changes** — Offer a re-run after they implement Quick Wins to show updated savings and remaining opportunities.
- **Coverage gaps** — If the tool doesn’t cover a high-spend service (see Coverage Gap Analysis), propose a targeted review (e.g. Redshift, OpenSearch, SageMaker) or CUR-based analysis.
- **Governance** — Suggest tagging and Cost Allocation Tags so future runs can segment by environment, team, or project.

---

## 8. Disclaimer (for customer communications)

When presenting, use wording consistent with your organisation’s policy. Example:

> *This review is based on automated analysis of the account at a point in time. Findings are recommendations only. The customer is responsible for validating each finding (e.g. confirming a resource is unused) and for any changes made to their environment. Savings estimates use list pricing and assumptions documented in the report; actual savings may vary.*

---

*This approach aligns the tool output with an AWS Pro Serv–style cost optimisation engagement: evidence-based, prioritised, and customer-ready.*
