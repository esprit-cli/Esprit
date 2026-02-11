---
name: aws
description: AWS cloud security testing covering S3 misconfigurations, IAM privilege escalation, IMDS exploitation, Lambda abuse, and Cognito weaknesses
---

# AWS Cloud Security

Amazon Web Services dominates cloud infrastructure and presents a massive attack surface spanning storage, identity, compute, and serverless services. Misconfigurations in S3, IAM, IMDS, Lambda, and Cognito are consistently the highest-impact findings in AWS penetration tests. Understanding the relationships between these services is critical because AWS privilege escalation typically chains multiple service interactions together.

## Architecture

AWS uses a shared responsibility model where the customer controls identity, access policies, network configuration, and data protection. The IAM layer governs all API access through policies attached to users, groups, roles, and resources. Every AWS API call is authenticated via SigV4 signatures derived from access keys or temporary credentials issued by STS. Services communicate through IAM roles, resource policies, and VPC networking. The metadata service (IMDS) provides instance-level credentials to EC2, ECS, and Lambda runtimes. Serverless compute (Lambda) runs in firecracker microVMs with execution roles that define their blast radius.

Key architectural elements for security testers:
- IAM evaluation logic: explicit deny > explicit allow > implicit deny; cross-account requires both sides to allow
- Resource policies (S3, SQS, Lambda) are evaluated independently from identity policies
- Service Control Policies (SCPs) set maximum permission boundaries across an AWS Organization
- Trust policies on roles define who can assume them; condition keys add constraints

## Attack Surface

- S3 buckets: public ACLs, overly permissive bucket policies, static website hosting, cross-account access
- IAM: users with inline policies, roles with broad trust relationships, unused access keys, missing MFA
- EC2/ECS/EKS: IMDS exposure, security group misconfigurations, public AMIs with embedded credentials
- Lambda: environment variable secrets, overly permissive execution roles, event injection via untrusted triggers
- API Gateway: missing authentication, broken authorization, request validation bypass
- Cognito: self-registration enabled, unverified attribute escalation, identity pool misconfigurations
- Secrets Manager / SSM Parameter Store: overly broad read permissions, unencrypted parameters
- CloudFront: origin access misconfiguration, cache poisoning, signed URL/cookie weaknesses
- SQS/SNS: resource policies with wildcard principals, message injection, subscription confirmation abuse
- DynamoDB: overly permissive table policies, scan operations exposing entire datasets
- ECR: public repositories, image pull without authentication, image poisoning

## High-Value Targets

- IAM credentials: long-lived access keys, STS temporary credentials, cross-account role assumptions
- S3 buckets containing PII, backups, configuration files, deployment artifacts, or Terraform state
- IMDS endpoint at 169.254.169.254 providing role credentials on any compute instance
- Secrets Manager and SSM Parameter Store entries with database passwords and API keys
- Lambda environment variables containing hardcoded tokens and connection strings
- CloudTrail logs (to understand monitoring and identify gaps in detection)
- KMS keys with overly permissive key policies allowing decrypt operations
- RDS snapshots shared publicly or cross-account without encryption
- EBS snapshots with sensitive data shared to other accounts or made public

## Reconnaissance

- Enumerate S3 buckets: naming conventions based on company name, environment, and service patterns
- `aws s3 ls s3://target-bucket --no-sign-request` for unauthenticated access testing
- `aws sts get-caller-identity` to confirm current identity and account context
- `aws iam list-users`, `list-roles`, `list-policies` to map the IAM landscape
- `aws lambda list-functions --region us-east-1` to discover serverless attack surface
- `aws cognito-idp list-user-pools --max-results 20` to find authentication endpoints
- Use `enumerate-iam` tool to brute-force allowed API actions for a given credential set
- Check CloudTrail configuration: `aws cloudtrail describe-trails` to identify logging gaps
- `aws ec2 describe-instances` to map compute resources and security group exposure

## Key Vulnerabilities

### S3 Bucket Misconfiguration

- Public ACLs: `s3:GetObject` granted to `AllUsers` or `AuthenticatedUsers` principal
- Bucket policy with `"Principal": "*"` allowing unauthenticated reads or writes
- Static website hosting exposing directory listings when index document is missing
- Enumerate with: `aws s3api get-bucket-acl --bucket target` and `get-bucket-policy`
- Write access enables defacement, malware hosting, or supply chain attacks via poisoned artifacts
- Versioning disabled means no recovery from destructive writes; enabled means old secrets are recoverable
- Server access logging to the same bucket creates recursive log amplification
- S3 Object Lock prevents deletion; check for compliance mode vs governance mode bypass
- Cross-region replication may sync sensitive data to less-protected buckets

### IAM Privilege Escalation

- `iam:PassRole` + `lambda:CreateFunction` + `lambda:InvokeFunction`: create Lambda with admin role, invoke it
- `iam:PassRole` + `ec2:RunInstances`: launch instance with privileged role, access IMDS
- `iam:CreatePolicyVersion`: create a new version of an existing managed policy with full admin permissions
- `iam:AttachUserPolicy` / `iam:AttachRolePolicy`: directly attach AdministratorAccess
- `iam:PutUserPolicy` / `iam:PutRolePolicy`: add inline policy granting `*:*`
- `sts:AssumeRole` with overly permissive trust policies allowing cross-account or wildcard principals
- `iam:CreateLoginProfile` / `iam:UpdateLoginProfile`: set console password for any user
- Chain: `iam:PassRole` -> `glue:CreateDevEndpoint` -> SSH into dev endpoint with passed role
- `iam:PassRole` + `codebuild:CreateProject` + `codebuild:StartBuild`: run builds with privileged role
- `lambda:UpdateFunctionCode` on existing functions: inject code that runs under the function execution role

### IMDS Exploitation

- IMDSv1: simple GET to `http://169.254.169.254/latest/meta-data/iam/security-credentials/ROLE_NAME`
- Returns temporary AccessKeyId, SecretAccessKey, and Token valid for up to 6 hours
- SSRF vulnerabilities in web applications running on EC2 directly expose these credentials
- IMDSv2 requires a PUT request with TTL header to obtain a session token first
- IMDSv2 bypass: if the SSRF sink follows redirects, use an attacker-controlled redirect that adds the PUT method and headers
- Check enforcement: `aws ec2 describe-instances --query 'Reservations[].Instances[].MetadataOptions'`
- ECS task credentials at `http://169.254.170.2$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`
- User data scripts at `/latest/user-data` may contain bootstrap secrets and configuration
- Network hop limit of 1 for IMDSv2 prevents containers from accessing IMDS through extra network hops

### Lambda and API Gateway

- Environment variables often contain database credentials, API keys, and JWT signing secrets
- `aws lambda get-function --function-name target` returns code download URL and environment config
- Event injection: untrusted input from S3 events, SQS messages, API Gateway without validation
- API Gateway: test for missing authorization on endpoints, method-level auth inconsistencies
- Lambda layers may contain shared secrets or vulnerable dependencies
- Execution role over-permissioning: Lambda with `s3:*` or `dynamodb:*` when it only needs reads
- Resource policies on Lambda functions may allow cross-account invocation
- API Gateway resource policies can expose private APIs if conditions are misconfigured

### Cognito Weaknesses

- Self-registration enabled allows attacker to create accounts in user pools
- Custom attributes writable by users: set `custom:role` to `admin` during sign-up
- Identity pools with unauthenticated role granting excessive AWS permissions
- Token manipulation: modify claims in ID token when signature verification is client-side only
- `aws cognito-idp sign-up --client-id ID --username attacker --password P@ssw0rd!`
- User enumeration via different error messages for existing vs non-existing usernames
- Hosted UI customization can be abused for phishing if custom domains are not locked down
- Cognito triggers (pre-sign-up, post-confirmation) with code injection if input is unsanitized

## Bypass Techniques

- S3 block public access can be set at account level but overridden per-bucket in some configurations
- Use `--no-sign-request` to test unauthenticated access even when you have valid credentials
- SCP (Service Control Policies) may block actions in the console but not via CLI/API in some edge cases
- Permission boundaries limit effective permissions but `iam:DeleteRolePermissionsBoundary` removes them
- Resource-based policies can grant access even when identity policies deny (cross-account especially)
- VPC endpoints with restrictive policies can be bypassed if the S3 bucket policy allows public access
- GuardDuty evasion: use stolen credentials from the same region, avoid known-bad IPs, throttle API calls
- CloudTrail gaps: data events (S3 object-level, Lambda invocations) often not logged by default
- Assume role session names are logged but can be set to any string, complicating attribution
- Use IP addresses from the same region as the target to reduce anomaly-based detection alerts

## Tooling

- **Pacu**: AWS exploitation framework with modules for privilege escalation, persistence, and data exfiltration
- **enumerate-iam**: brute-force AWS API permissions for a given set of credentials
- **ScoutSuite**: multi-cloud security auditing tool with comprehensive AWS rule sets
- **Prowler**: AWS security best practices assessment and compliance auditing
- **S3Scanner**: discover and test S3 bucket permissions at scale
- **CloudMapper**: visualize AWS environments and identify network exposure
- **Rhino Security Labs escalation paths**: reference for 20+ IAM privilege escalation techniques
- **aws-vault**: secure credential management during testing engagements
- **Cloudfox**: discover exploitable attack paths in AWS infrastructure
- **WeirdAAL**: AWS attack library with modules for reconnaissance and exploitation
- **Steampipe**: SQL-based querying of AWS resources for security analysis

## Testing Methodology

1. **Credential discovery** - Search for exposed access keys in source code, CI/CD configs, environment variables, and public repositories
2. **Identity mapping** - Run `sts get-caller-identity`, enumerate attached policies, and identify effective permissions
3. **S3 assessment** - List all buckets, test each for public read/write/list with and without authentication
4. **IAM escalation analysis** - Map all roles and policies looking for PassRole, CreatePolicyVersion, and Attach* permissions
5. **IMDS testing** - From any compute context, attempt metadata access; verify IMDSv2 enforcement
6. **Serverless review** - Enumerate Lambda functions, inspect execution roles, check for secrets in env vars and layers
7. **Cognito testing** - Attempt self-registration, attribute manipulation, and unauthenticated identity pool access
8. **Network exposure** - Review security groups, NACLs, and VPC endpoints for overly permissive rules
9. **Logging and detection** - Identify CloudTrail, GuardDuty, and Config coverage gaps
10. **Persistence assessment** - Check for backdoor IAM users, modified trust policies, and hidden Lambda triggers

## Validation Requirements

1. Demonstrate credential access with `sts get-caller-identity` using any obtained credentials
2. Show S3 data access with actual object retrieval (use benign test files, never exfiltrate real data)
3. Prove IAM escalation by documenting the full chain from initial to elevated permissions
4. Confirm IMDS access by retrieving the role name and temporary credential metadata (redact secrets)
5. Validate Lambda findings by showing environment variable contents or code download capability
6. Document the blast radius: what resources the escalated permissions can access

## False Positives

- S3 buckets intentionally public for static website hosting or CDN origins
- IAM roles with broad permissions but restricted by SCPs, permission boundaries, or session policies
- IMDS accessible but IMDSv2 enforced and no SSRF vector exists to perform the PUT token exchange
- Cognito self-registration enabled but with post-confirmation Lambda triggers that enforce approval workflows
- Cross-account roles with strict external ID requirements preventing confused deputy attacks

## Impact

- Full account compromise through IAM privilege escalation chains
- Mass data exfiltration from misconfigured S3 buckets containing PII or intellectual property
- Credential theft via IMDS enabling lateral movement across the AWS environment
- Supply chain attacks through write access to deployment artifact buckets
- Persistent backdoor access via IAM user creation, role trust modification, or Lambda backdoors
- Compliance violations (HIPAA, PCI-DSS, SOC2) from public storage or weak access controls
- Cryptojacking through EC2 instance launch or Lambda abuse using compromised credentials
- Data destruction or ransomware through S3 object deletion when versioning and MFA delete are disabled

## Pro Tips

1. Always start with `sts get-caller-identity` to understand your current context before any enumeration
2. Check for S3 bucket versioning: `aws s3api list-object-versions --bucket target` can reveal deleted secrets
3. IAM policy simulator (`iam simulate-principal-policy`) can confirm escalation paths without triggering actions
4. Look for `NotAction` and `NotResource` in IAM policies: these are commonly misconfigured deny-with-exceptions
5. Lambda functions triggered by S3 events can be exploited by uploading crafted objects to trigger buckets
6. Always check for `aws:SourceIp` conditions in policies: they often only apply to console, not API/CLI access
7. Cross-account enumeration: `sts assume-role` attempts reveal whether roles exist even when assumption fails
8. Use `aws configure list` and check `~/.aws/credentials` on compromised hosts for additional profiles

## Summary

AWS security testing centers on the interplay between identity, storage, and compute services. The most critical findings chain IAM misconfigurations with service-specific weaknesses: PassRole to Lambda for privilege escalation, SSRF to IMDS for credential theft, and overly permissive S3 policies for data exposure. Effective AWS penetration testing requires understanding how IAM policies, resource policies, SCPs, and permission boundaries interact to define the actual blast radius of any discovered weakness.
