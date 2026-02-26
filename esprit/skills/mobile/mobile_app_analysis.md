---
name: mobile-app-analysis
description: Static and dynamic analysis of Android APK and iOS IPA mobile applications for security vulnerabilities, endpoint extraction, and API surface discovery
---

# Mobile App Analysis

Mobile application security testing extracts attack surface from compiled app artifacts. The primary goal is to identify backend API endpoints, hardcoded secrets, insecure configurations, and client-side vulnerabilities that can be validated through the existing web/API testing pipeline.

## Analysis Pipeline

### Phase 1: Static Artifact Processing

**Android APK Analysis**

1. Decompile the APK using available tools in the sandbox:
   - `apktool d <app.apk> -o <output_dir>` — decode resources, smali
   - `jadx -d <output_dir> <app.apk>` — decompile to Java source
   - `unzip <app.apk> -d <output_dir>` — extract raw contents

2. Extract key artifacts:
   - `AndroidManifest.xml` — permissions, activities, exported components, deep links
   - `res/xml/network_security_config.xml` — cleartext traffic policy, certificate pinning
   - `classes.dex` → decompiled Java/Kotlin source
   - `assets/` and `res/raw/` — embedded configs, certificates, databases

3. Endpoint and secret extraction:
   - Search decompiled source for URL patterns: `https?://[^\s"']+`
   - Search for API base URLs, path constants, retrofit/volley annotations
   - Search for hardcoded credentials: API keys, tokens, passwords
   - Search for Firebase/AWS/GCP configuration values
   - Search for JWT tokens or signing keys

**iOS IPA Analysis**

1. Extract the IPA (it's a ZIP archive):
   - `unzip <app.ipa> -d <output_dir>`
   - The binary is in `Payload/<AppName>.app/`

2. Extract key artifacts:
   - `Info.plist` — URL schemes, permissions, ATS configuration
   - Embedded frameworks and dylibs
   - `embedded.mobileprovision` — provisioning profile details
   - Asset catalogs and storyboards

3. String extraction:
   - `strings Payload/<AppName>.app/<binary>` — extract all strings from Mach-O
   - Search for URL patterns, API endpoints, hardcoded secrets
   - Analyze embedded plists for configuration values

### Phase 2: Endpoint-to-API Correlation

After extracting endpoints from the mobile app:

1. **Catalog extracted endpoints** — organize by base URL, method, path pattern
2. **Correlate with proxy traffic** — check if endpoints appear in captured requests
3. **Identify untested endpoints** — endpoints found in app but not in proxy traffic are high-value targets
4. **Generate API test requests** — use `send_request` to probe discovered endpoints
5. **Check authorization** — test endpoints with/without auth tokens found in the app

### Phase 3: Configuration and Component Analysis

**Android-specific checks:**
- Exported activities/services/receivers without proper permissions
- `android:debuggable="true"` in manifest
- `android:allowBackup="true"` enabling data extraction
- Cleartext traffic allowed (`cleartextTrafficPermitted`)
- Missing certificate pinning
- Weak or custom crypto implementations
- WebView JavaScript interface exposure (`@JavascriptInterface`)
- Intent redirection vulnerabilities
- Deep link URI validation

**iOS-specific checks:**
- ATS exceptions allowing insecure connections
- Missing certificate pinning
- Keychain access group misconfigurations
- URL scheme handling without validation
- Pasteboard data leakage risks

## Key Patterns to Search

```
# API endpoints and URLs
grep -rn "https\?://[^\"' ]*" <source_dir> --include="*.java" --include="*.kt" --include="*.swift" --include="*.m"

# Hardcoded secrets
grep -rni "api[_-]?key\|secret[_-]?key\|password\|token\|auth" <source_dir> --include="*.java" --include="*.kt" --include="*.xml" --include="*.plist"

# Firebase config
grep -rn "firebase\|firebaseio\.com\|googleapis\.com" <source_dir>

# AWS resources
grep -rn "AKIA[A-Z0-9]\{16\}\|amazonaws\.com\|s3\..*\.amazonaws" <source_dir>

# Base64-encoded content (potential embedded secrets)
grep -rn "[A-Za-z0-9+/]\{40,\}=" <source_dir> --include="*.java" --include="*.kt" --include="*.xml"
```

## Reporting Notes

- Reference the exact file and line where vulnerabilities were found in decompiled source
- For hardcoded secrets: verify they are functional before reporting (test against the API)
- For endpoint findings: always attempt to access the endpoint and document the response
- Mobile findings should include the platform (Android/iOS) and the specific artifact source
