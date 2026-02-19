# AWS Cost Optimisation Tool

A production-ready Python CLI tool that audits your AWS account for cost waste, analyses spend trends via AWS Cost Explorer, and generates a CTO-presentation-ready PDF report with charts, prioritised recommendations, and ROI calculations.

## Features

- **Live AWS Pricing**: Fetches current on-demand prices from the AWS Price List API with 24hr in-memory cache and hardcoded fallback values.
- **Cost Explorer Integration**: Pulls configurable months of actual spend by service, computes month-over-month trends, detects anomalies, and forecasts next month's spend.
- **CE-driven service discovery**: Identifies top-billed services and flags coverage gaps where no detailed analyser exists.
- **Multi-region scanning**: Automatically discovers and scans all enabled AWS regions in parallel.
- **12 service analysers** (cost-related only; IAM excluded):
  - EC2 (idle instances, unattached EBS volumes, unused Elastic IPs, old snapshots)
  - RDS (idle databases, Multi-AZ non-prod, old snapshots)
  - S3 (buckets missing lifecycle rules)
  - Lambda (unused functions, over-provisioned memory)
  - ELB (load balancers with no healthy targets, low traffic)
  - CloudWatch (log groups without retention, stale alarms)
  - NAT Gateway (idle gateways, high-traffic VPC endpoint opportunities)
  - DynamoDB (idle tables, over-provisioned capacity, missing auto-scaling)
  - ElastiCache (idle clusters, oversized nodes)
  - ECS/Fargate (idle services, over-provisioned tasks, Spot opportunities)
  - ECR (repositories without lifecycle policy, untagged images)
  - Data Transfer (high internet egress, inter-region, cross-AZ costs)
- **Recommendation engine** with:
  - Categorisation: Quick Win / Strategic / Long-term
  - ROI calculation: annualised savings ÷ implementation cost
  - Per-recommendation implementation steps
  - Risk assessment with callout boxes
- **CTO-level PDF report** with:
  - Cover page with account metadata and total savings
  - Executive summary with dynamic narrative, current vs optimized spend chart
  - **Savings Roadmap**: priority matrix table, ROI summary, 12-month projection chart
  - Spend trend charts (line, stacked bar, sparklines)
  - Service cost breakdown table
  - **Coverage Gap Analysis**: heatmap of top billed services with analyser coverage status
  - Per-service findings with risk callouts and implementation steps
  - **Commitment-Based Savings Analysis**: RI/Savings Plans candidates
- **JSON export**: Structured JSON output alongside PDF (`--format pdf json`)
- **Production hardening**: adaptive retry/throttle handling, permission-aware error handling, file logging

## For AWS Pro Serv / consultants

If you are running this as an **AWS Professional Services** cost optimisation review, see **[Cost Review Approach](docs/COST_REVIEW_APPROACH.md)** for engagement scope, how to run the analysis, how to interpret and present the report, and a customer-facing prioritisation framework.

## Requirements

- Python 3.10+
- AWS credentials configured (environment variables, instance profile, or a named profile in `~/.aws/credentials`)
- The credentials must have the IAM permissions listed below

## Installation

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

```bash
# Basic usage (uses default credential chain: env vars, instance profile, or ~/.aws/credentials [default])
python main.py

# Use a named AWS profile
python main.py --profile my-profile

# Custom output path
python main.py --profile my-profile --output /tmp/aws-cost-report.pdf

# Scan specific regions only
python main.py --regions us-east-1 eu-west-1

# Analyse specific services only
python main.py --services ec2 rds s3

# Only include findings with estimated savings above $10/month
python main.py --min-saving 10

# Use 3 months of Cost Explorer history instead of 6
python main.py --months 3
```

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | *(optional)* | AWS named profile; if omitted, uses default credential chain (env vars, instance profile, etc.) |
| `--output` | `aws-cost-report-YYYY-MM-DD.pdf` | Output PDF file path |
| `--regions` | all enabled | Space-separated list of regions to scan |
| `--services` | all | Space-separated list of services to analyse |
| `--min-saving` | `0` | Minimum estimated monthly saving (USD) to include a finding |
| `--months` | `6` | Months of Cost Explorer history to retrieve |
| `--max-workers` | `10` | Max parallel threads for region scanning |
| `--format` | `pdf` | Output formats: `pdf`, `json`, or both |
| `--debug` | off | Enable debug logging |
| `--quiet` | off | Suppress INFO-level console output |

## Required IAM Permissions

The AWS credentials (profile or default chain) need the following read-only permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetCostForecast",
        "pricing:GetProducts",
        "ec2:Describe*",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:DescribeAlarms",
        "rds:Describe*",
        "s3:ListAllMyBuckets",
        "s3:GetBucketLifecycleConfiguration",
        "s3:GetBucketLocation",
        "lambda:ListFunctions",
        "lambda:GetFunctionConfiguration",
        "elasticloadbalancing:Describe*",
        "logs:DescribeLogGroups",
        "sts:GetCallerIdentity",
        "dynamodb:ListTables",
        "dynamodb:DescribeTable",
        "application-autoscaling:DescribeScalableTargets",
        "elasticache:DescribeCacheClusters",
        "elasticache:DescribeReplicationGroups",
        "ecs:ListClusters",
        "ecs:ListServices",
        "ecs:DescribeServices",
        "ecs:DescribeTaskDefinition"
      ],
      "Resource": "*"
    }
  ]
}
```

> **Note:** `pricing:GetProducts` is optional. If not granted, the tool falls back to hardcoded prices with a warning.

## Output

The tool generates a PDF report at the specified output path (default: `aws-cost-report-YYYY-MM-DD.pdf`). With `--format pdf json`, a JSON export is also written alongside the PDF.

## Running Tests

```bash
# Install dev dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_models.py -v
```

## Project Structure

```
aws-cost-optimisation/
├── main.py                    # CLI entry point, orchestration, recommendation engine
├── config.py                  # Configuration dataclass with validation
├── aws_client.py              # Boto3 session factory and region discovery
├── models.py                  # Data models (Finding, Recommendation, ScanResult, etc.)
├── docs/
│   └── COST_REVIEW_APPROACH.md  # AWS Pro Serv cost review approach and deliverables
├── analysers/
│   ├── base.py                # Abstract base analyser with permission-aware error handling
│   ├── cost_explorer.py       # AWS Cost Explorer trends, forecasts, service discovery
│   ├── ec2.py
│   ├── rds.py
│   ├── s3.py
│   ├── lambda_.py
│   ├── elb.py
│   ├── cloudwatch.py
│   ├── nat_gateway.py
│   ├── dynamodb.py
│   ├── elasticache.py
│   ├── ecs.py
│   ├── ecr.py
│   └── data_transfer.py
├── report/
│   ├── charts.py              # matplotlib chart generation (8 chart types)
│   ├── pdf_builder.py         # ReportLab PDF assembly (9 sections)
│   └── json_exporter.py       # Structured JSON export
├── utils/
│   ├── pricing.py             # Live AWS Pricing API client with TTL cache
│   ├── cost_estimator.py      # Savings estimation helpers
│   ├── service_registry.py    # Centralised service name/alias registry
│   └── exceptions.py          # Custom exception hierarchy
└── tests/
    ├── conftest.py             # Shared fixtures
    ├── test_models.py
    ├── test_cost_estimator.py
    ├── test_pricing.py
    ├── test_recommendation_engine.py
    └── test_pdf_builder.py
```
