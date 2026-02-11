---
name: gcp
description: GCP cloud security testing covering Cloud Storage ACLs, service account exploitation, metadata service abuse, Cloud Functions, and Firebase weaknesses
---

# GCP Cloud Security

Google Cloud Platform's security model revolves around projects, IAM bindings, and service accounts. Unlike AWS where IAM policies are standalone documents, GCP uses IAM bindings that attach roles directly to members on resources. Service accounts serve as both identity and credential mechanisms, making their compromise particularly devastating. Firebase integration adds another layer of attack surface through client-side database rules and misconfigured authentication.

## Architecture

GCP organizes resources within a hierarchy: organization, folders, and projects. IAM policies inherit downward through this hierarchy, so a binding at the organization level applies to all projects beneath it. Service accounts are the primary machine identity, each associated with a unique email address and optionally long-lived JSON key files. The metadata service on GCE instances provides OAuth2 access tokens for the attached service account. Cloud Functions execute in managed environments with default or custom service accounts. Firebase operates as a layer on top of GCP, sharing the same project and IAM infrastructure but exposing client-accessible APIs for Firestore, Realtime Database, Storage, and Authentication.

Key architectural elements for security testers:
- IAM bindings are additive; deny policies exist but are opt-in and rarely deployed, so any allow binding typically grants access unless an explicit deny policy overrides it
- Predefined roles bundle permissions but custom roles can include arbitrary permission sets
- Organization policies set guardrails that cannot be overridden by IAM bindings at lower levels
- Workload Identity Federation enables external identities to impersonate service accounts without keys

## Attack Surface

- Cloud Storage buckets: public ACLs, uniform vs fine-grained access, signed URL misuse
- Service accounts: leaked JSON key files, over-permissioned default accounts, impersonation chains
- GCE instances: metadata service exposure, startup scripts with secrets, public IPs with weak firewall rules
- Cloud Functions: environment variable secrets, overly broad execution roles, unauthenticated invocation
- Firebase: open Firestore/RTDB rules, API key exposure, misconfigured authentication providers
- GKE: node service account permissions, workload identity misconfiguration, RBAC gaps
- Cloud SQL: public IP with authorized networks set to 0.0.0.0/0, weak credentials
- Secret Manager: overly permissive accessor bindings, unrotated secrets
- Pub/Sub: message injection, subscription hijacking, dead letter queue exposure
- Cloud Run: unauthenticated services, container image pulling, environment variable exposure
- BigQuery: dataset sharing with `allUsers`, public datasets leaking internal analytics data

## High-Value Targets

- Service account JSON key files containing long-lived private keys
- GCE metadata endpoint at `http://metadata.google.internal/computeMetadata/v1/`
- OAuth2 access tokens from the metadata service or application default credentials
- Cloud Storage buckets with Terraform state, database backups, or deployment artifacts
- Secret Manager versions containing database passwords and API keys
- Firebase configuration objects in client-side JavaScript with project ID and API key
- Firestore and Realtime Database with permissive security rules
- Cloud Build configurations with embedded secrets or privileged service accounts
- Service account impersonation chains leading to project owner-equivalent access
- GKE cluster admin credentials and kubeconfig files on developer workstations
- Cloud Build trigger configurations with access to source repositories and deployment targets

## Reconnaissance

- `gcloud projects list` to enumerate accessible projects
- `gcloud iam service-accounts list --project PROJECT` to discover service accounts
- `gcloud storage ls` and `gcloud storage ls gs://BUCKET` for bucket enumeration
- Predictable bucket names: `PROJECT-ID-backup`, `PROJECT-ID-assets`, `staging.PROJECT-ID.appspot.com`
- `gcloud compute instances list` to map compute resources and external IPs
- `gcloud functions list --project PROJECT` to discover serverless endpoints
- Firebase project discovery: check `https://PROJECT-ID-default-rtdb.firebaseio.com/.json`
- `gcloud asset search-all-iam-policies --scope=projects/PROJECT` for comprehensive IAM mapping
- Test unauthenticated bucket access: `curl https://storage.googleapis.com/BUCKET_NAME`
- `gcloud services list --enabled --project PROJECT` to understand which APIs are active

## Key Vulnerabilities

### Cloud Storage Misconfiguration

- `allUsers` or `allAuthenticatedUsers` granted `storage.objectViewer` or `storage.objectAdmin`
- Uniform bucket-level access disabled allows per-object ACL overrides that may be more permissive
- Signed URLs with excessive expiration times or generated with overly privileged service accounts
- Bucket-level permissions checked with: `gcloud storage buckets get-iam-policy gs://BUCKET`
- Object listing via: `curl https://storage.googleapis.com/storage/v1/b/BUCKET/o`
- CORS misconfiguration allowing cross-origin reads from attacker-controlled domains
- Bucket names are globally unique and publicly discoverable; deleted bucket names can be reclaimed
- Versioning allows retrieval of deleted objects: `gcloud storage objects list gs://BUCKET --all-versions`
- Object lifecycle rules may delete evidence of exposure; check for retention policies
- Customer-managed encryption keys (CMEK) with overly broad decrypt permissions negate encryption benefit

### Service Account Key Leakage

- JSON key files committed to version control, embedded in Docker images, or stored in CI/CD configs
- Default service accounts (PROJECT_NUMBER-compute@developer.gserviceaccount.com) often have Editor role
- `gcloud iam service-accounts keys list --iam-account SA_EMAIL` reveals active keys
- Key files never expire unless explicitly deleted; no automatic rotation mechanism
- Compromised keys provide persistent access independent of any network controls
- Service account impersonation: `gcloud auth print-access-token --impersonate-service-account=SA_EMAIL`
- Token generation: `iam.serviceAccounts.getAccessToken` permission enables impersonation without key file
- Chain: impersonate SA with `roles/owner` through intermediate SA with `iam.serviceAccountTokenCreator`
- `iam.serviceAccountKeys.create` permission allows generating new keys for any service account
- Workload Identity Federation misconfiguration can allow external identities to impersonate internal SAs

### GCE Metadata Service

- Endpoint: `http://metadata.google.internal/computeMetadata/v1/`
- Required header: `Metadata-Flavor: Google` (blocks basic SSRF but not all vectors)
- Access token: `/instance/service-accounts/default/token` returns OAuth2 token
- Project metadata: `/project/attributes/` may contain SSH keys, startup scripts, custom metadata
- Instance metadata: `/instance/attributes/` including `startup-script` with potential secrets
- Recursive listing: append `?recursive=true` to enumerate all metadata at once
- SSRF with header injection bypasses the `Metadata-Flavor` requirement
- `kube-env` attribute on GKE nodes contains bootstrap credentials for the kubelet
- Service account scopes limit token capabilities: check `/instance/service-accounts/default/scopes`

### Cloud Functions Exploitation

- Unauthenticated invocation when `allUsers` has `cloudfunctions.functions.invoke` binding
- Check: `gcloud functions get-iam-policy FUNCTION_NAME --region REGION`
- Environment variables: `gcloud functions describe FUNCTION_NAME` reveals runtime configuration
- Source code download via Cloud Source Repositories or connected GitHub repos
- Event injection through Pub/Sub triggers, Cloud Storage triggers, or HTTP endpoints
- Default service account typically has Project Editor role with broad access
- Function source stored in Cloud Storage bucket: `gcf-sources-PROJECT_NUMBER-REGION`
- Cold start timing differences can leak information about function configuration
- Gen2 Functions (Cloud Run-based) inherit Cloud Run security characteristics and IMDS patterns

### Firebase Integration Security

- Firestore rules set to `allow read, write: if true;` permit unauthenticated data access
- Test: `curl https://firestore.googleapis.com/v1/projects/PROJECT/databases/(default)/documents/COLLECTION`
- Realtime Database open rules: `curl https://PROJECT.firebaseio.com/.json`
- Firebase API keys are designed to be public but overly permissive API restrictions expand blast radius
- Self-registration enabled by default in Firebase Auth; test account creation and role assignment
- Firebase Storage rules often mirror Firestore rules and may share the same misconfiguration patterns
- Client SDK configuration in JavaScript source reveals project ID, API key, and auth domain
- Cloud Firestore in Datastore mode has different access patterns but same IAM underpinnings
- Firebase Hosting misconfigurations can serve attacker-controlled content on legitimate domains
- Firebase Dynamic Links abuse for phishing through trusted domain redirects

## Bypass Techniques

- Organization policy constraints can be circumvented if the constraint is not applied at the right hierarchy level
- VPC Service Controls can be bypassed through permitted projects or access levels
- IAM conditions using `request.time` or `resource.name` can be satisfied by timing or targeting specific resources
- `iam.serviceAccounts.actAs` is the gatekeeper for service account usage; look for it on unexpected principals
- Domain-restricted sharing constraint bypass: use a service account within the allowed domain
- Audit log evasion: data access logs are often not enabled for all services by default
- Access token from metadata service can be used from any network location for the token lifetime
- Custom roles may include dangerous permissions not present in predefined roles
- Compute Engine serial port output (`gcloud compute instances get-serial-port-output`) can leak boot secrets
- Application Default Credentials (ADC) search order can be exploited by placing credentials in expected paths

## Tooling

- **gcloud CLI**: primary tool for all GCP enumeration and exploitation
- **ScoutSuite**: multi-cloud security auditing with GCP-specific modules
- **GCPBucketBrute**: discover and test Cloud Storage bucket permissions
- **Hayat**: GCP cloud security auditing and hardening script with IAM checks
- **Cartography**: infrastructure mapping including GCP resources and relationships
- **gcp-firewall-enum**: enumerate firewall rules and identify exposed services
- **firebase-tools CLI**: interact with Firebase services for security testing
- **Forseti Security**: GCP resource inventory and policy scanner (open source)
- **gcpdiag**: diagnostic tool that identifies common GCP misconfigurations
- **gcp-iam-collector**: collect and analyze IAM bindings across projects
- **PMapper**: IAM privilege escalation path analysis (supports GCP)

## Testing Methodology

1. **Project enumeration** - Identify all accessible projects, understand the organization hierarchy
2. **IAM analysis** - Map all service accounts, their key status, bindings, and impersonation chains
3. **Storage assessment** - Test every bucket for public access, list permissions, and object-level ACLs
4. **Metadata exploitation** - From any GCE context, query the metadata service for tokens and project attributes
5. **Serverless review** - Enumerate Cloud Functions, check invocation permissions, inspect environment variables
6. **Firebase testing** - Locate Firebase config, test Firestore/RTDB rules, attempt self-registration
7. **Network exposure** - Review firewall rules, public IPs, and VPC Service Controls perimeter
8. **Secret discovery** - Check Secret Manager access, environment variables, instance metadata, startup scripts
9. **Privilege escalation** - Identify impersonation chains and `setIamPolicy` permissions for escalation

## Validation Requirements

1. Confirm service account compromise by calling `gcloud auth print-access-token` with obtained credentials
2. Demonstrate storage access by listing or reading objects from misconfigured buckets
3. Show metadata access by retrieving the access token endpoint with proper headers
4. Validate Firebase issues by reading or writing data through the REST API without authentication
5. Document the service account impersonation chain with each step and the resulting access level
6. Prove Cloud Function access by triggering unauthenticated invocation and capturing the response

## False Positives

- Buckets intentionally public for static asset hosting with no sensitive content
- Service account keys that are actively rotated and monitored with alerts on usage
- Firebase API keys exposed in client code (by design) with properly restrictive security rules
- Metadata service accessible but scopes restricted to minimum required for the workload
- Unauthenticated Cloud Functions that serve as public APIs by design with proper input validation
- `allAuthenticatedUsers` bindings that are acceptable for internal Google Workspace-scoped resources

## Impact

- Full project compromise through service account key theft or impersonation chain escalation
- Mass data exfiltration from misconfigured Cloud Storage buckets or Firebase databases
- Persistent access through service account key generation that survives password resets and MFA changes
- Lateral movement across projects through cross-project IAM bindings and shared service accounts
- Supply chain attacks via write access to Cloud Build artifacts or Cloud Functions source buckets
- Customer data breach through open Firestore rules exposing entire database collections
- Cryptocurrency mining via compromised compute instances or Cloud Functions
- Regulatory violations from public datasets or cross-border data exposure without proper controls

## Pro Tips

1. Default compute service account has Editor role; any SSRF on a GCE instance likely yields broad access
2. `gcloud projects get-ancestors PROJECT` reveals the full organizational hierarchy for policy inheritance
3. Check `iam.serviceAccounts.actAs` permission: it is the universal gatekeeper for service account usage
4. Firebase Realtime Database rules can be read directly at `/.settings/rules.json` if permissions allow
5. Service account key creation leaves an audit trail but key usage from external IPs may not be monitored
6. GKE workload identity replaces node SA; if not configured, all pods share the node service account
7. `gcloud asset search-all-resources` provides a comprehensive inventory faster than per-service enumeration
8. Look for `setIamPolicy` permission on any resource: it allows granting yourself any role on that resource

## Summary

GCP security testing revolves around the service account model and the resource hierarchy. The most impactful findings chain service account key leakage or metadata token theft with overly permissive IAM bindings to achieve project-wide compromise. Firebase integration adds a client-accessible attack surface where misconfigured security rules expose entire databases. Effective GCP testing requires understanding how IAM inheritance, service account impersonation, and workload identity interact across the organization, folder, and project boundaries.
