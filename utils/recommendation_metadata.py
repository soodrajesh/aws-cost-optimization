"""
Recommendation metadata: effort/risk mapping and human-readable titles per finding type.
Used by the recommendation engine in main.py.
"""

EFFORT_MAP: dict[str, dict] = {
    # EC2
    "idle_instance": {
        "effort": "medium", "hours": 2, "risk": "medium",
        "risk_notes": "Low CPU does not mean unused — verify workload before any change. Prefer right-sizing or scheduler over termination.",
        "category": "strategic",
        "steps": [
            "Review instance metrics, connected resources, and whether the instance is actually unused",
            "If still needed: right-size to a smaller instance type or use AWS Instance Scheduler (e.g. 10hrs/day weekdays)",
            "If genuinely unused: stop instance (saves ~50%) or, after confirmation, terminate",
            "Confirm cost reduction in next billing cycle",
        ],
    },
    "stopped_instance": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Stopped instances still incur EBS costs; terminating removes all charges.",
        "category": "quick_win",
        "steps": [
            "Verify the instance has been stopped for the expected duration",
            "Check if any AMI or snapshot should be retained",
            "Terminate instance: aws ec2 terminate-instances --instance-ids <id>",
        ],
    },
    "unattached_ebs": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "Verify no AMIs reference these volumes before deleting.",
        "category": "quick_win",
        "steps": [
            "Verify no snapshots or AMIs reference the volume",
            "Delete volume: aws ec2 delete-volume --volume-id <id>",
            "Confirm deletion in Cost Explorer after 1 billing cycle",
        ],
    },
    "unused_eip": {
        "effort": "low", "hours": 0.25, "risk": "low",
        "risk_notes": "",
        "category": "quick_win",
        "steps": [
            "Verify the EIP is not referenced in DNS records or security configurations",
            "Release EIP: aws ec2 release-address --allocation-id <id>",
        ],
    },
    "old_snapshot": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "Ensure snapshots are not required for compliance or recovery.",
        "category": "quick_win",
        "steps": [
            "Review snapshot age and source volume",
            "Implement snapshot lifecycle policy with AWS DLM",
            "Delete old snapshots: aws ec2 delete-snapshot --snapshot-id <id>",
        ],
    },
    # RDS
    "idle_rds": {
        "effort": "medium", "hours": 2, "risk": "medium",
        "risk_notes": "Verify no applications depend on the database before stopping/deleting.",
        "category": "strategic",
        "steps": [
            "Confirm zero connections via CloudWatch DatabaseConnections metric",
            "Check application configurations for database references",
            "Take a final snapshot before action",
            "Stop (saves ~50% — auto-restarts after 7 days) or delete the instance",
        ],
    },
    "multi_az_non_prod": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Disabling Multi-AZ removes automatic failover.",
        "category": "quick_win",
        "steps": [
            "Confirm the instance is non-production",
            "Modify instance: aws rds modify-db-instance --db-instance-identifier <id> --no-multi-az",
            "Schedule a maintenance window for the change",
        ],
    },
    "old_rds_snapshot": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "",
        "category": "quick_win",
        "steps": [
            "Review snapshot age and relevance",
            "Delete: aws rds delete-db-snapshot --db-snapshot-identifier <id>",
        ],
    },
    # S3 — practical optimisations (new)
    "s3_mpu_no_abort": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "Aborting incomplete MPU parts is safe; they are not accessible as complete objects.",
        "category": "quick_win",
        "steps": [
            "Add an AbortIncompleteMultipartUpload lifecycle rule to all buckets: DaysAfterInitiation=7",
            "aws s3api put-bucket-lifecycle-configuration with AbortIncompleteMultipartUpload rule",
            "Run one-time cleanup of existing incomplete MPU: aws s3api list-multipart-uploads --bucket <name> | then abort each",
            "Automate with AWS Config rule to alert if any bucket lacks this rule",
        ],
    },
    # S3 (legacy key — kept for backwards compatibility)
    "no_lifecycle": {
        "effort": "medium", "hours": 2, "risk": "low",
        "risk_notes": "Lifecycle transitions may affect access patterns for infrequently accessed data.",
        "category": "strategic",
        "steps": [
            "Analyse bucket access patterns using S3 Storage Lens or S3 Analytics",
            "Design lifecycle rules (e.g., transition to IA after 30 days, Glacier after 90)",
            "Apply lifecycle configuration via Console or CLI",
            "Monitor transition metrics over the next billing cycle",
        ],
    },
    # Lambda
    "idle_lambda": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "",
        "category": "quick_win",
        "steps": [
            "Verify function is not triggered by scheduled events or other services",
            "Delete function: aws lambda delete-function --function-name <name>",
        ],
    },
    "overprovisioned_lambda": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Monitor performance after reducing memory to ensure no timeouts.",
        "category": "quick_win",
        "steps": [
            "Review actual memory usage from CloudWatch MaxMemoryUsed metric",
            "Update memory configuration with 20% headroom above peak usage",
            "aws lambda update-function-configuration --function-name <name> --memory-size <mb>",
            "Test function performance after the change",
        ],
    },
    # ELB
    "no_targets": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "Verify no DNS records point to the load balancer.",
        "category": "quick_win",
        "steps": [
            "Check DNS and Route 53 for references to the load balancer",
            "Delete load balancer via Console or CLI",
        ],
    },
    "low_traffic_elb": {
        "effort": "medium", "hours": 2, "risk": "medium",
        "risk_notes": "Low traffic may be legitimate for internal or seasonal services.",
        "category": "strategic",
        "steps": [
            "Review traffic patterns over a longer period",
            "Consider consolidating with other load balancers",
            "If unused, delete after verifying no dependencies",
        ],
    },
    # CloudWatch
    "log_group_no_retention": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "Setting retention will permanently delete older logs.",
        "category": "quick_win",
        "steps": [
            "Review compliance requirements for log retention",
            "Set retention policy (e.g., 30, 60, or 90 days) via Console or CLI",
            "aws logs put-retention-policy --log-group-name <name> --retention-in-days 90",
        ],
    },
    "stale_alarm": {
        "effort": "low", "hours": 0.25, "risk": "low",
        "risk_notes": "",
        "category": "quick_win",
        "steps": [
            "Verify the alarm is monitoring a resource that no longer exists",
            "Delete alarm: aws cloudwatch delete-alarms --alarm-names <name>",
        ],
    },
    # IAM
    "unused_role": {
        "effort": "medium", "hours": 1, "risk": "medium",
        "risk_notes": "Deleting a role may break services that assume it.",
        "category": "strategic",
        "steps": [
            "Check IAM Access Analyzer for any recent role assumption",
            "Review role trust policy and attached policies",
            "Delete role after confirming no active usage",
        ],
    },
    "old_access_key": {
        "effort": "low", "hours": 0.5, "risk": "medium",
        "risk_notes": "Rotating keys may break applications using the old key.",
        "category": "strategic",
        "steps": [
            "Create a new access key for the user",
            "Update all applications using the old key",
            "Deactivate the old key, wait for confirmation, then delete",
        ],
    },
    # NAT Gateway
    "idle_nat_gateway": {
        "effort": "medium", "hours": 2, "risk": "medium",
        "risk_notes": "Deleting a NAT Gateway will break outbound internet access for private subnets.",
        "category": "strategic",
        "steps": [
            "Verify no resources in associated private subnets need internet access",
            "Update route tables to remove NAT Gateway routes",
            "Delete NAT Gateway: aws ec2 delete-nat-gateway --nat-gateway-id <id>",
            "Release the associated Elastic IP if no longer needed",
        ],
    },
    "high_traffic_nat_gateway": {
        "effort": "high", "hours": 8, "risk": "low",
        "risk_notes": "VPC endpoint setup requires security group and route table changes.",
        "category": "long_term",
        "steps": [
            "Analyse traffic patterns to identify top destinations (S3, DynamoDB, ECR, etc.)",
            "Create VPC Gateway Endpoints for S3 and DynamoDB (free)",
            "Create VPC Interface Endpoints for other high-traffic services",
            "Update route tables and security groups",
            "Monitor NAT Gateway throughput reduction after endpoint deployment",
        ],
    },
    "duplicate_nat_gateway": {
        "effort": "medium", "hours": 2, "risk": "medium",
        "risk_notes": "Consolidation may affect redundancy within the AZ.",
        "category": "strategic",
        "steps": [
            "Review routing tables for each NAT Gateway",
            "Consolidate routes to a single NAT Gateway per AZ",
            "Delete the redundant NAT Gateway",
        ],
    },
    # DynamoDB
    "idle_dynamodb_table": {
        "effort": "medium", "hours": 2, "risk": "medium",
        "risk_notes": "Verify no applications reference this table before deletion.",
        "category": "strategic",
        "steps": [
            "Confirm zero read/write activity over the analysis period",
            "Export table data to S3 if archival is needed",
            "Delete table: aws dynamodb delete-table --table-name <name>",
        ],
    },
    "overprovisioned_dynamodb": {
        "effort": "medium", "hours": 4, "risk": "medium",
        "risk_notes": "Under-provisioning may cause throttling. Consider enabling auto-scaling instead.",
        "category": "strategic",
        "steps": [
            "Review consumed vs provisioned capacity trends",
            "Enable auto-scaling with appropriate min/max bounds",
            "Or reduce provisioned capacity manually with monitoring",
            "Consider switching to on-demand billing if traffic is unpredictable",
        ],
    },
    "no_autoscaling_dynamodb": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "",
        "category": "quick_win",
        "steps": [
            "Enable auto-scaling via Console or CLI",
            "Set target utilisation to 70% with appropriate min/max bounds",
        ],
    },
    # ElastiCache
    "idle_elasticache": {
        "effort": "medium", "hours": 2, "risk": "medium",
        "risk_notes": "Applications may have hardcoded cache endpoints.",
        "category": "strategic",
        "steps": [
            "Verify no applications are configured to use this cluster",
            "Take a final backup if needed",
            "Delete cluster: aws elasticache delete-cache-cluster --cache-cluster-id <id>",
        ],
    },
    "oversized_elasticache": {
        "effort": "medium", "hours": 4, "risk": "medium",
        "risk_notes": "Downsizing may cause brief connectivity interruption. Schedule during maintenance window.",
        "category": "strategic",
        "steps": [
            "Review current node type vs utilisation metrics",
            "Select a smaller node type that fits the workload",
            "Modify cluster during a maintenance window",
            "Monitor cache hit rate and latency after the change",
        ],
    },
    # ECS/Fargate
    "idle_ecs_service": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "",
        "category": "quick_win",
        "steps": [
            "Verify the service is not expected to scale back up",
            "Delete service: aws ecs delete-service --cluster <cluster> --service <service> --force",
        ],
    },
    "overprovisioned_fargate": {
        "effort": "medium", "hours": 2, "risk": "low",
        "risk_notes": "Monitor performance after right-sizing to ensure no OOM errors.",
        "category": "strategic",
        "steps": [
            "Create a new task definition revision with reduced CPU/memory",
            "Update the service to use the new task definition",
            "Monitor CPU and memory utilisation after the change",
        ],
    },
    "fargate_spot_opportunity": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Fargate Spot tasks may be interrupted with 2 minutes notice.",
        "category": "quick_win",
        "steps": [
            "Add FARGATE_SPOT capacity provider to the service",
            "Set a capacity provider strategy with FARGATE_SPOT as primary",
            "Ensure the application handles graceful shutdowns",
        ],
    },
    # Data Transfer
    "high_internet_egress": {
        "effort": "high", "hours": 16, "risk": "low",
        "risk_notes": "",
        "category": "long_term",
        "steps": [
            "Analyse egress traffic patterns by service and destination",
            "Implement CloudFront for static content delivery",
            "Use S3 Transfer Acceleration for large file transfers",
            "Consider AWS PrivateLink for inter-service communication",
        ],
    },
    "high_inter_region_transfer": {
        "effort": "high", "hours": 16, "risk": "medium",
        "risk_notes": "Architectural changes may affect latency and reliability.",
        "category": "long_term",
        "steps": [
            "Identify services communicating across regions",
            "Evaluate co-locating resources in the same region",
            "Use VPC Peering or Transit Gateway for optimised routing",
            "Review caching strategies to reduce repeated cross-region calls",
        ],
    },
    "high_cross_az_transfer": {
        "effort": "medium", "hours": 8, "risk": "low",
        "risk_notes": "",
        "category": "strategic",
        "steps": [
            "Identify high-bandwidth communication pairs across AZs",
            "Co-locate communicating resources in the same AZ where possible",
            "Use placement groups for latency-sensitive workloads",
        ],
    },
    # EC2 — EBS IOPS optimisation (new)
    "ebs_overprovisioned_iops": {
        "effort": "low", "hours": 1, "risk": "medium",
        "risk_notes": "Reducing IOPS on io1/io2 can cause performance degradation if actual peak IOPS exceed the new provisioned level. Monitor IOPSUtilization after the change.",
        "category": "quick_win",
        "steps": [
            "Review CloudWatch VolumeReadOps and VolumeWriteOps over a 30-day period to understand peak usage",
            "Modify volume IOPS: aws ec2 modify-volume --volume-id <id> --iops <new-value>",
            "Changes apply in the background with no downtime; monitor VolumeQueueLength after the change",
            "Consider switching from io2 to gp3 if IOPS requirement is ≤ 16,000 (gp3 provides 3,000 free + up to 16,000 at lower cost)",
        ],
    },
    # EC2 — practical optimisations (new)
    "ec2_rightsizing": {
        "effort": "medium", "hours": 3, "risk": "low",
        "risk_notes": "Test workload performance at the smaller size before committing in production.",
        "category": "quick_win",
        "steps": [
            "Review 14-day CPU and memory utilisation metrics in CloudWatch",
            "Use AWS Compute Optimizer recommendations as a second opinion",
            "Stop the instance and change instance type via Console or CLI: "
            "aws ec2 modify-instance-attribute --instance-id <id> --instance-type <new-type>",
            "Start the instance and monitor performance for 48 hours",
            "Repeat for any remaining over-sized instances",
        ],
    },
    "ec2_scheduler": {
        "effort": "low", "hours": 2, "risk": "low",
        "risk_notes": "Ensure automated start before business hours so developers are not blocked.",
        "category": "quick_win",
        "steps": [
            "Deploy AWS Instance Scheduler (CloudFormation template from AWS Solutions Library)",
            "Create a schedule: Mon–Fri 08:00–18:00 local time (saves ~65% vs 24/7)",
            "Tag non-prod EC2 instances with 'scheduler:schedule' = <schedule-name>",
            "Test start/stop via the scheduler before relying on it",
            "Alternatively use EventBridge rules + Lambda for custom on/off schedules",
        ],
    },
    "ec2_graviton": {
        "effort": "medium", "hours": 4, "risk": "low",
        "risk_notes": "Test application compatibility on arm64; most x86 software runs unmodified or needs a recompile.",
        "category": "strategic",
        "steps": [
            "Launch a Graviton instance alongside the current instance for testing",
            "Run your application workload and compare performance benchmarks",
            "If compatible, stop the current instance and change instance type to the Graviton equivalent",
            "Update any compiled binaries or container images to arm64 architecture",
            "Consider Graviton for all new instance launches going forward",
        ],
    },
    "ec2_spot_opportunity": {
        "effort": "medium", "hours": 4, "risk": "medium",
        "risk_notes": "Spot instances can be interrupted with 2-minute notice; only suitable for fault-tolerant or stateless workloads.",
        "category": "strategic",
        "steps": [
            "Identify stateless or fault-tolerant workloads (build servers, batch jobs, dev/test)",
            "Use EC2 Auto Scaling Groups with mixed instance types and Spot capacity",
            "Set up Spot interruption handling (drain tasks, save state, use instance metadata)",
            "Start with a Spot Fleet or ASG using 'lowest-price' or 'capacity-optimized' strategy",
            "Monitor Spot interruption rates and add On-Demand fallback if needed",
        ],
    },
    "ebs_gp2_to_gp3": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "gp3 volumes can be modified in place with no downtime required.",
        "category": "quick_win",
        "steps": [
            "List all gp2 volumes: aws ec2 describe-volumes --filters Name=volume-type,Values=gp2",
            "Modify each volume: aws ec2 modify-volume --volume-id <id> --volume-type gp3",
            "gp3 baseline: 3,000 IOPS and 125 MB/s — increase if your workload requires more",
            "Changes take effect immediately with no reboot; monitor volume modification status",
            "Automate with a script or AWS Config rule for future volumes",
        ],
    },
    # RDS — practical optimisations (new)
    "rds_rightsizing": {
        "effort": "medium", "hours": 3, "risk": "medium",
        "risk_notes": "Downsizing RDS requires a brief instance modification window; schedule during low-traffic period.",
        "category": "quick_win",
        "steps": [
            "Review CPU, connections, and FreeableMemory in CloudWatch over 14+ days",
            "Use AWS Compute Optimizer or RDS recommendations in Cost Explorer",
            "Modify instance class: aws rds modify-db-instance --db-instance-identifier <id> --db-instance-class <new-class> --apply-immediately",
            "Monitor database performance for 48 hours post-change",
            "Repeat for remaining over-provisioned instances",
        ],
    },
    "rds_scheduler": {
        "effort": "low", "hours": 2, "risk": "low",
        "risk_notes": "RDS automatically restarts after 7 days if stopped via AWS console; Instance Scheduler handles this.",
        "category": "quick_win",
        "steps": [
            "Deploy AWS Instance Scheduler (supports RDS as well as EC2)",
            "Create a schedule: Mon–Fri 08:00–18:00 (saves ~65% vs 24/7)",
            "Tag non-prod RDS instances with 'scheduler:schedule' = <schedule-name>",
            "Alternatively use EventBridge rules to call: "
            "aws rds stop-db-instance / start-db-instance on a cron schedule",
            "Test the schedule in dev before applying to QA/staging",
        ],
    },
    "rds_aurora_serverless": {
        "effort": "high", "hours": 16, "risk": "medium",
        "risk_notes": "Migration requires testing Aurora compatibility; some MySQL/PostgreSQL features differ slightly.",
        "category": "long_term",
        "steps": [
            "Create an Aurora Serverless v2 cluster from an RDS snapshot for testing",
            "Validate application queries and stored procedures against Aurora",
            "Benchmark Aurora Serverless v2 performance under your traffic patterns",
            "Use AWS DMS or native snapshot restore to migrate with minimal downtime",
            "Set Aurora Serverless v2 min ACU = 0.5, max ACU = appropriate ceiling",
            "Monitor ACU consumption vs old instance cost for 2-4 weeks",
        ],
    },
    "rds_gp2_to_gp3": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Storage modification applies in the background; no downtime required.",
        "category": "quick_win",
        "steps": [
            "Identify RDS instances using gp2 storage",
            "Modify storage type: aws rds modify-db-instance --db-instance-identifier <id> --storage-type gp3 --apply-immediately",
            "gp3 default: 3,000 IOPS and 125 MB/s — provision more IOPS/throughput if needed",
            "Validate performance after modification; monitor ReadLatency/WriteLatency",
        ],
    },
    # Lambda — practical optimisations (new)
    "lambda_memory_rightsizing": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Monitor performance after reducing memory to ensure no timeouts.",
        "category": "quick_win",
        "steps": [
            "Review actual memory usage from CloudWatch MaxMemoryUsed metric",
            "Update memory configuration with 20% headroom above peak usage",
            "aws lambda update-function-configuration --function-name <name> --memory-size <mb>",
            "Test function under production-like load after the change",
        ],
    },
    "lambda_graviton": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Most Python, Node.js, Java, and .NET functions run on arm64 without code changes.",
        "category": "quick_win",
        "steps": [
            "Update function architecture: aws lambda update-function-configuration --function-name <name> --architectures arm64",
            "If using container images, rebuild for linux/arm64 and push to ECR",
            "Run a smoke test to confirm correctness on arm64",
            "Monitor duration and error metrics — Graviton typically runs 10-19% faster",
        ],
    },
    "lambda_deprecated_runtime": {
        "effort": "medium", "hours": 4, "risk": "high",
        "risk_notes": "AWS blocks code updates on functions using deprecated runtimes; migrate urgently.",
        "category": "strategic",
        "steps": [
            "Identify all functions using deprecated runtimes",
            "Update runtime in the function configuration and retest",
            "For Python: update import paths and syntax for newer Python versions",
            "For Node.js: update to async/await patterns and review package compatibility",
            "Run integration tests before promoting to production",
        ],
    },
    # S3 — practical optimisations (new)
    "s3_no_lifecycle": {
        "effort": "medium", "hours": 2, "risk": "low",
        "risk_notes": "Lifecycle transitions may affect access patterns; review before applying.",
        "category": "strategic",
        "steps": [
            "Analyse bucket access patterns using S3 Storage Lens or S3 Analytics (enable for 30+ days)",
            "Create a lifecycle rule: transition to S3-IA after 30 days, Glacier Instant Retrieval after 90 days",
            "Add a delete rule for objects older than your retention policy",
            "Apply via Console or CLI: aws s3api put-bucket-lifecycle-configuration --bucket <name> --lifecycle-configuration file://lifecycle.json",
            "Monitor storage class distribution monthly in S3 Storage Lens",
        ],
    },
    "s3_intelligent_tiering": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Objects < 128KB are not eligible for IT and remain in Standard; small objects are not charged the IT monitoring fee.",
        "category": "quick_win",
        "steps": [
            "Enable S3 Intelligent-Tiering on the bucket with a lifecycle rule: StorageClass=INTELLIGENT_TIERING after 0 days",
            "Optionally configure the Archive Access tier (90 days) and Deep Archive tier (180 days) for additional savings",
            "aws s3api put-bucket-lifecycle-configuration with INTELLIGENT_TIERING storage class",
            "Monitor the IT storage class distribution and savings in S3 Storage Lens",
        ],
    },
    "s3_version_expiry": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Expired versions are permanently deleted; verify compliance retention requirements first.",
        "category": "quick_win",
        "steps": [
            "Add a NoncurrentVersionExpiration rule to the lifecycle policy (e.g., expire after 30 days)",
            "Also add an AbortIncompleteMultipartUpload rule to clean up failed uploads",
            "Review current non-current version count via S3 Storage Lens",
            "Apply the lifecycle rule and monitor storage reduction over the next 30 days",
        ],
    },
    # Lambda — new checks
    "lambda_excessive_timeout": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "Reducing timeout too aggressively can cause legitimate long-running invocations to fail. Set to 3x the p99 duration, not the average.",
        "category": "quick_win",
        "steps": [
            "Check CloudWatch Duration p99 metric (not just average) over 30 days",
            "Set timeout to 3x the p99 duration value as a safe limit",
            "aws lambda update-function-configuration --function-name <name> --timeout <seconds>",
            "Set up a CloudWatch alarm on Duration metric to alert if invocations approach the new timeout",
        ],
    },
    "lambda_high_error_rate": {
        "effort": "medium", "hours": 4, "risk": "medium",
        "risk_notes": "High error rates cause automatic retries on async/SQS triggers, potentially processing the same message multiple times.",
        "category": "strategic",
        "steps": [
            "Check CloudWatch Errors and Throttles metrics to distinguish error types",
            "Review CloudWatch Logs Insights for error patterns: filter @message like /ERROR/ | stats count by @message",
            "Fix application bugs causing errors (timeouts, memory OOM, unhandled exceptions)",
            "Add Dead Letter Queues (DLQ) to capture failed async invocations",
            "Implement proper error handling and retries in the function code",
            "If throttling-related: request Lambda concurrency limit increase or implement exponential back-off",
        ],
    },
    # ECS — new checks
    "fargate_graviton": {
        "effort": "medium", "hours": 3, "risk": "low",
        "risk_notes": "Most containerized applications run on ARM64 without changes; verify your base Docker image supports linux/arm64.",
        "category": "quick_win",
        "steps": [
            "Rebuild container images for linux/arm64: docker buildx build --platform linux/arm64 -t <image>:arm64 .",
            "Push arm64 image to ECR: docker push <ecr-uri>:arm64",
            "Create a new task definition revision with: runtimePlatform.cpuArchitecture = ARM64",
            "Update the ECS service to use the new task definition revision",
            "Monitor CPU and memory utilisation after the change — Graviton often runs faster too",
        ],
    },
    # ECR — new service
    "ecr_no_lifecycle": {
        "effort": "low", "hours": 1, "risk": "low",
        "risk_notes": "Lifecycle policies delete images permanently. Ensure CI/CD does not depend on specific image tags that may be expired.",
        "category": "quick_win",
        "steps": [
            "Create a lifecycle policy to expire untagged images after 1 day",
            "Add a rule to keep only the last 10 tagged images (adjust to your retention needs)",
            "aws ecr put-lifecycle-policy --repository-name <name> --lifecycle-policy-text file://policy.json",
            "Example policy: expire untagged after 1 day, keep last 10 tagged images",
            "Run aws ecr describe-images --repository-name <name> to audit current images first",
        ],
    },
    "ecr_untagged_images": {
        "effort": "low", "hours": 0.5, "risk": "low",
        "risk_notes": "Untagged images are not in use by any running container; safe to delete.",
        "category": "quick_win",
        "steps": [
            "List untagged images: aws ecr list-images --repository-name <name> --filter tagStatus=UNTAGGED",
            "Batch delete: aws ecr batch-delete-image --repository-name <name> --image-ids imageTag=untagged",
            "Add a lifecycle policy to prevent future accumulation (expire untagged after 1 day)",
        ],
    },
    # Savings Plans / RI (for high-spend services without findings)
    "savings_plan": {
        "effort": "medium", "hours": 8, "risk": "medium",
        "risk_notes": "Commitment-based pricing requires accurate usage forecasting. Under-commitment wastes potential; over-commitment locks in unused capacity.",
        "category": "strategic",
        "steps": [
            "Analyse usage patterns over the past 6-12 months",
            "Use AWS Cost Explorer Savings Plans recommendations",
            "Start with a conservative commitment (1-year, no upfront)",
            "Monitor utilisation and adjust coverage over time",
        ],
    },
}


# Human-readable recommendation titles when a single finding type is present.
RECOMMENDATION_TYPE_NAMES: dict[str, str] = {
    "idle_instance": "Right-size or schedule low-CPU EC2 instances",
    "stopped_instance": "Remove long-stopped EC2 instances",
    "unattached_ebs": "Delete unattached EBS volumes",
    "unused_eip": "Release unused Elastic IPs",
    "old_snapshot": "Delete old EBS snapshots",
    "idle_rds": "Stop or delete idle RDS instances",
    "multi_az_non_prod": "Disable Multi-AZ on non-production RDS",
    "old_rds_snapshot": "Delete old RDS snapshots",
    "s3_mpu_no_abort": "Add AbortIncompleteMultipartUpload lifecycle rule to S3 buckets",
    "no_lifecycle": "Add S3 lifecycle policies",
    "idle_lambda": "Remove unused Lambda functions",
    "overprovisioned_lambda": "Right-size Lambda function memory",
    "no_targets": "Delete load balancers with no healthy targets",
    "low_traffic_elb": "Review low-traffic load balancers",
    "log_group_no_retention": "Set retention on CloudWatch log groups",
    "stale_alarm": "Delete orphaned CloudWatch alarms",
    "unused_role": "Remove unused IAM roles",
    "old_access_key": "Rotate old IAM access keys",
    "idle_nat_gateway": "Delete idle NAT Gateways",
    "high_traffic_nat_gateway": "Deploy VPC endpoints to reduce NAT Gateway traffic",
    "duplicate_nat_gateway": "Consolidate duplicate NAT Gateways",
    "idle_dynamodb_table": "Delete idle DynamoDB tables",
    "overprovisioned_dynamodb": "Right-size DynamoDB provisioned capacity",
    "no_autoscaling_dynamodb": "Enable DynamoDB auto-scaling",
    "idle_elasticache": "Delete idle ElastiCache clusters",
    "oversized_elasticache": "Downsize ElastiCache nodes",
    "idle_ecs_service": "Remove idle ECS services",
    "overprovisioned_fargate": "Right-size Fargate task definitions",
    "fargate_spot_opportunity": "Switch non-production Fargate to Spot",
    "high_internet_egress": "Reduce internet data transfer egress",
    "high_inter_region_transfer": "Optimise inter-region data transfer",
    "high_cross_az_transfer": "Reduce cross-AZ data transfer",
    "ebs_overprovisioned_iops": "Right-size EBS provisioned IOPS",
    "ec2_rightsizing": "Right-size underutilised EC2 instances",
    "ec2_scheduler": "Schedule non-production EC2 instances",
    "ec2_graviton": "Migrate EC2 to Graviton (ARM)",
    "ec2_spot_opportunity": "Use Spot instances for fault-tolerant workloads",
    "ebs_gp2_to_gp3": "Migrate EBS gp2 volumes to gp3",
    "rds_rightsizing": "Right-size underutilised RDS instances",
    "rds_scheduler": "Schedule non-production RDS instances",
    "rds_aurora_serverless": "Evaluate Aurora Serverless v2 for variable workloads",
    "rds_gp2_to_gp3": "Migrate RDS storage from gp2 to gp3",
    "lambda_memory_rightsizing": "Right-size Lambda memory configuration",
    "lambda_graviton": "Migrate Lambda to Graviton (arm64)",
    "lambda_deprecated_runtime": "Migrate Lambda off deprecated runtimes",
    "s3_no_lifecycle": "Add S3 lifecycle and storage class transitions",
    "s3_intelligent_tiering": "Enable S3 Intelligent-Tiering",
    "s3_version_expiry": "Add S3 version expiry and multipart abort rules",
    "lambda_excessive_timeout": "Right-size Lambda timeout",
    "lambda_high_error_rate": "Address Lambda high error rate",
    "fargate_graviton": "Migrate Fargate tasks to Graviton (ARM64)",
    "ecr_no_lifecycle": "Add ECR lifecycle policy to repositories",
    "ecr_untagged_images": "Clean up ECR untagged images",
}
