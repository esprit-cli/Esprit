---
name: azure
description: Azure cloud security testing covering Blob storage exposure, Entra ID misconfiguration, managed identity abuse, Key Vault access, and Azure Functions exploitation
---

# Azure Cloud Security

Microsoft Azure's security model is deeply intertwined with Entra ID (formerly Azure AD), making identity the central pillar of both authentication and authorization. Azure resources are organized into management groups, subscriptions, resource groups, and individual resources, with RBAC policies inheriting downward. Managed identities replace the need for stored credentials but introduce their own exploitation paths. The tight integration between Azure, Microsoft 365, and Entra ID means a single misconfiguration can cascade across the entire Microsoft ecosystem.

## Architecture

Azure's control plane uses Azure Resource Manager (ARM) for all resource operations, authenticated through Entra ID OAuth2 tokens. The data plane for individual services (Blob Storage, Key Vault, SQL) uses separate authentication mechanisms but still relies on Entra ID for token issuance. RBAC assignments consist of a security principal, a role definition, and a scope (management group, subscription, resource group, or resource). Managed identities provide automatic credential rotation by having the Azure platform issue tokens on behalf of the resource. Entra ID serves triple duty as the directory service, identity provider, and authorization engine for both Azure resources and Microsoft 365 services.

Key architectural elements for security testers:
- Control plane (ARM) and data plane access require different RBAC roles and different tokens
- Entra ID tenants can span multiple subscriptions; compromising the tenant affects all linked subscriptions
- Conditional Access policies are the primary MFA and device compliance enforcement mechanism
- Azure Lighthouse enables cross-tenant management, creating potential lateral movement paths

## Attack Surface

- Blob Storage: anonymous container access, shared access signatures (SAS), storage account key exposure
- Entra ID: tenant misconfiguration, guest user over-permissioning, application registration abuse, consent grant attacks
- Managed identities: system-assigned and user-assigned identity exploitation via IMDS
- Key Vault: overly permissive access policies, soft-deleted secret recovery, RBAC vs vault access policy conflicts
- Azure Functions: anonymous authentication level, managed identity abuse, environment variable secrets
- App Service: exposed SCM/Kudu endpoints, deployment credentials, managed identity tokens
- Virtual Machines: IMDS at 169.254.169.254, custom script extensions with secrets, serial console access
- Azure DevOps: service connection credentials, variable groups with secrets, pipeline injection
- Cosmos DB: primary key exposure, overly broad RBAC, cross-account access
- Azure SQL: firewall rules allowing 0.0.0.0/0, Azure AD-only auth disabled, transparent data encryption gaps
- Logic Apps: workflow injection, connector credential exposure, managed identity abuse

## High-Value Targets

- Entra ID Global Administrator role and privileged role assignments
- Storage account access keys (full control over all containers and blobs)
- Key Vault secrets containing database connection strings, API keys, and certificates
- Managed identity tokens from the IMDS endpoint on any Azure compute resource
- Azure DevOps service connections with Contributor or Owner on production subscriptions
- Entra ID application registrations with high-privilege API permissions and client secrets
- Automation account RunAs certificates and credentials
- ARM templates and Bicep files with embedded secrets in parameter files
- Subscription-level Reader or above role assignments for reconnaissance
- Azure Container Registry images with embedded credentials or vulnerable dependencies

## Reconnaissance

- `az account list` to enumerate accessible subscriptions and tenants
- `az ad user list` and `az ad group list` to map the Entra ID directory
- `az role assignment list --all` to understand RBAC across the subscription
- `az storage account list` to discover all storage accounts and their configurations
- `az keyvault list` to enumerate Key Vaults and their access models
- `az functionapp list` to discover Azure Functions and their authentication settings
- `az vm list` to map virtual machines and their identity assignments
- External enumeration: `https://ACCOUNT.blob.core.windows.net/CONTAINER?restype=container&comp=list`
- Tenant discovery: `https://login.microsoftonline.com/DOMAIN/.well-known/openid-configuration`
- AADInternals: `Invoke-AADIntReconAsOutsider -DomainName target.com` for external tenant recon

## Key Vulnerabilities

### Blob Storage Anonymous Access

- Container public access levels: blob (individual blob URLs) or container (full listing and read)
- Check: `az storage container list --account-name ACCOUNT --auth-mode login --query "[?properties.publicAccess!='none']"`
- Storage account setting `allowBlobPublicAccess` must be true for container-level settings to take effect
- SAS tokens: check for overly permissive tokens (full permissions, no IP restriction, long expiry)
- Account-level SAS vs service-level SAS vs user delegation SAS have different revocation capabilities
- Storage account keys provide full unrestricted access; key rotation invalidates all SAS tokens signed with the rotated key, but there is no way to revoke individual SAS tokens without rotating the key
- Enumerate blobs: `https://ACCOUNT.blob.core.windows.net/CONTAINER?restype=container&comp=list`
- Shared key authorization can be disabled per storage account forcing Entra ID auth only
- Check for storage accounts with `minimumTlsVersion` below 1.2
- Static website hosting on `$web` container may expose directory listings or sensitive files

### Entra ID / Azure AD Misconfiguration

- Guest users with default permissions can enumerate all users, groups, and applications in the tenant
- Application registrations with `Application.ReadWrite.All` or `RoleManagement.ReadWrite.Directory` permissions
- Admin consent grants giving applications excessive Microsoft Graph API permissions
- Overly permissive app registration: any user can register applications (`Users can register applications` = Yes)
- Dangerous default: all users can consent to apps requesting low-risk permissions (user consent settings)
- Conditional Access policy gaps: policies not covering all users, apps, or sign-in scenarios
- PRT (Primary Refresh Token) theft enabling device-bound session hijacking
- Password spray attacks against accounts without MFA (check sign-in logs for legacy auth protocols)
- Privileged role holders without PIM (Privileged Identity Management) just-in-time activation
- B2B collaboration settings allowing any external user to be invited as a guest
- Dynamic groups with membership rules that can be manipulated to gain access to privileged groups

### Managed Identity Exploitation

- IMDS endpoint: `http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/`
- Required header: `Metadata: true`
- System-assigned identities are tied to the resource lifecycle; user-assigned persist independently
- SSRF on Azure App Service, Functions, or VMs can extract managed identity tokens
- Tokens are bearer tokens valid for the resource specified; request tokens for multiple resources
- Common resources: `https://management.azure.com/`, `https://vault.azure.net/`, `https://graph.microsoft.com/`
- App Service exposes identity tokens via `IDENTITY_ENDPOINT` and `IDENTITY_HEADER` environment variables
- Managed identities with Contributor on the subscription can escalate privileges through indirect paths (custom script extensions, automation runbooks, or deploying resources that grant further access)
- Token lifetime is typically 24 hours for management plane tokens

### Key Vault Access

- Two access models: vault access policy (legacy) and Azure RBAC (recommended but inconsistently applied)
- `az keyvault secret list --vault-name VAULT` to enumerate secrets if you have list permission
- `az keyvault secret show --vault-name VAULT --name SECRET` to read secret values
- Soft-deleted vaults and secrets can be recovered: `az keyvault secret recover` and `az keyvault recover`
- Purge protection prevents permanent deletion; without it, attackers can destroy secrets
- Network ACLs on Key Vault can be bypassed if the managed identity is from a trusted service
- Key Vault certificates contain both the certificate and private key
- Access policy model: a single policy grants all or nothing for keys, secrets, and certificates per principal
- Diagnostic settings may log secret access operations to Log Analytics

### Azure Functions Exploitation

- Authentication levels: anonymous (no auth), function (function key), admin (master key)
- Anonymous functions are internet-accessible without any authentication
- Function keys stored in Azure Storage: discoverable if storage account is compromised
- Master key grants access to all functions and admin endpoints including `/admin/host/status`
- `az functionapp function keys list --name APP --function-name FUNC --resource-group RG`
- Managed identity tokens accessible from within function execution context
- Environment variables via `az functionapp config appsettings list` may contain secrets
- Kudu/SCM endpoint at `FUNCAPP.scm.azurewebsites.net` exposes deployment logs and environment
- Function proxies can be abused for SSRF if they proxy user-controlled URLs
- Durable Functions orchestration state can leak workflow data and intermediate results

## Bypass Techniques

- Conditional Access policies often exclude break-glass accounts or service principals
- Legacy authentication protocols (IMAP, POP3, SMTP) may bypass MFA requirements
- Managed identity tokens can be used from any network location once extracted
- Azure RBAC `Owner` cannot access Key Vault data plane; separate Key Vault RBAC roles are required
- Resource locks (CanNotDelete, ReadOnly) prevent modification but do not restrict data plane reads
- NSG (Network Security Group) rules are stateful: established connections bypass new deny rules
- Custom RBAC roles can include `Microsoft.Authorization/roleAssignments/write` for privilege escalation
- Azure Policy operates in audit-only mode by default; check for enforce vs audit effect
- PIM (Privileged Identity Management) eligible assignments can be activated without approval if misconfigured
- Tenant-to-tenant migration can leave orphaned permissions and stale access configurations
- Management groups with inherited RBAC allow escalation from child subscription to sibling subscription resources

## Tooling

- **az CLI**: primary tool for Azure resource enumeration and exploitation
- **AzureHound / BloodHound**: map Entra ID attack paths and privilege escalation routes
- **ROADtools**: Entra ID enumeration and token manipulation toolkit
- **MicroBurst**: Azure-specific security assessment PowerShell toolkit
- **ScoutSuite**: multi-cloud security auditing with Azure-specific rules
- **Stormspotter**: Azure infrastructure visualization and attack path mapping
- **TokenTactics**: Azure/Entra ID token manipulation and refresh token abuse
- **AADInternals**: comprehensive Entra ID/Azure AD exploitation toolkit
- **PowerZure**: Azure exploitation and post-exploitation PowerShell framework
- **Azurite**: local Azure Storage emulator useful for testing SAS token handling
- **GraphRunner**: Microsoft Graph API post-exploitation toolkit

## Testing Methodology

1. **Tenant reconnaissance** - Identify the Entra ID tenant, enumerate domains, and discover external-facing sign-in endpoints
2. **Identity assessment** - Map users, groups, roles, and service principals; identify privileged accounts without MFA
3. **RBAC analysis** - Enumerate role assignments at all scopes; identify paths to Owner, Contributor, or User Access Administrator
4. **Storage audit** - Test every storage account for public blob access, weak SAS tokens, and exposed account keys
5. **Key Vault review** - Check access policies vs RBAC, enumerate accessible secrets, test soft-delete recovery
6. **Managed identity testing** - From any compute context, extract tokens for management, graph, and vault resources
7. **Functions assessment** - Identify anonymous functions, test for key leakage, and review managed identity permissions
8. **Entra ID application review** - Audit app registrations, API permissions, consent grants, and client secret/certificate status
9. **Conditional Access validation** - Test policy coverage across all authentication flows and user types

## Validation Requirements

1. Demonstrate token acquisition by decoding JWT and showing the audience, roles, and expiration
2. Prove storage access by listing or downloading blobs from exposed containers
3. Confirm Key Vault access by showing secret names (not values unless authorized by engagement scope)
4. Validate managed identity exploitation by showing the token claims and demonstrating API access
5. Document Entra ID misconfigurations with specific settings and their security implications
6. Show the complete attack path from initial access to the highest achieved privilege level

## False Positives

- Storage accounts intentionally public for static website hosting via `$web` container
- Guest users with permissions restricted by Entra ID external collaboration settings
- Anonymous Azure Functions serving as public webhooks with proper input validation
- Managed identities with minimal RBAC scoped to specific resource groups only
- Key Vault access policies granting list but not get permission (can see names but not values)
- Conditional Access policies with compensating controls not visible in the policy configuration

## Impact

- Tenant-wide compromise through Entra ID Global Administrator escalation
- Mass data exfiltration from exposed Blob Storage containers and Key Vault secrets
- Lateral movement to Microsoft 365 services through Entra ID token abuse
- Persistent access via application registration with client secrets and high-privilege API permissions
- Ransomware deployment through Contributor access and custom script extensions on VMs
- Supply chain compromise through Azure DevOps pipeline manipulation
- Compliance violations (GDPR, HIPAA) from exposed PII in storage or databases

## Pro Tips

1. Entra ID tokens for `management.azure.com` can enumerate all subscriptions and resources the identity can access
2. `az ad app credential reset` adds a new client secret to any app registration you have Owner rights on
3. Check for user-assigned managed identities shared across multiple resources: compromising one compromises all
4. ARM template deployment history (`az deployment group list`) may contain parameter values including secrets
5. Azure Automation RunAs accounts use certificates that can be extracted for persistent access
6. `az rest --method GET --url` allows raw ARM API calls for accessing preview features not yet in az CLI
7. Custom script extensions on VMs run as SYSTEM/root and their content is stored in the VM's storage
8. Look for storage accounts with `allowSharedKeyAccess: true` combined with exposed connection strings

## Summary

Azure security testing is fundamentally an identity-driven exercise. Entra ID sits at the center of all authentication and authorization, meaning a single identity misconfiguration can cascade across Azure resources, Microsoft 365, and connected SaaS applications. The most impactful findings chain managed identity token theft with overly permissive RBAC to achieve subscription-wide control, or exploit Entra ID application registrations to gain persistent, MFA-resistant access to the entire tenant. Effective Azure testing requires understanding the duality of control plane (ARM) and data plane access, and how RBAC, Key Vault policies, and Conditional Access interact to define the actual security boundary.
