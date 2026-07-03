# Recording a live demo

This is a runbook, not a recording — written so it's ready to execute once an AWS
account exists. Unlike a Terraform-based demo, there's no cleanup/teardown step to
worry about here: this tool is read-only end to end (see "Required IAM permissions"
in the README — it's all `Describe*`/`List*`/`Get*`), so running it against a real
account costs nothing beyond the account's existing Cost Explorer/Price List API
calls, and creates zero infrastructure.

## Prerequisites

- An AWS account (free tier or otherwise) with at least a few resources in it —
  the demo is more convincing with something for the tool to actually find. A fresh,
  empty free-tier account will still run end to end, but the report will mostly show
  "no findings," which is honest but less illustrative than a demo scan against an
  account with a stopped-but-not-terminated instance, an idle EBS volume, or an S3
  bucket with no lifecycle policy lying around.
- AWS credentials configured locally (`aws configure` or a named profile) with the
  read-only IAM policy from the README's "Required IAM permissions" section attached.
- `asciinema` for a terminal recording (`brew install asciinema`), or a screen
  recording tool if you'd rather capture the PDF output visually too (arguably more
  useful for this repo than a terminal-only recording, since the actual output is a
  PDF report a reviewer can page through).

## Steps

```bash
git clone https://github.com/soodrajesh/aws-cost-optimization.git
cd aws-cost-optimization
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# start recording here
asciinema rec demo.cast

# a full scan against a named profile, both PDF and JSON output
python main.py --profile my-profile --format pdf json --months 3
```

`--months 3` keeps the Cost Explorer lookback shorter for a faster demo run than the
default 6. If the account has resources spread across specific regions you want to
highlight, add `--regions us-east-1 eu-west-1` to keep the run fast and the report
focused rather than scanning every enabled region.

Once it finishes, open the generated PDF and page through it on camera — the
executive summary, the savings roadmap with quick-win/strategic/long-term tags, and
the coverage-gap section (which high-spend services were *not* checked) are the
parts that actually demonstrate judgment, not just "a script ran."

## What to actually publish

`asciinema upload demo.cast` gives a shareable terminal-recording link. For the PDF
output itself, redact anything account-specific (account ID, resource IDs, real
dollar figures if the account isn't a throwaway) before including a screenshot or
the PDF itself anywhere public — a screen recording narrating a page-through of a
redacted report is probably the most useful artifact here, more so than the raw
terminal output. Link whatever you produce from the README's Usage section.
