---
name: web-content-discovery
description: Web content discovery techniques covering directory brute-forcing, backup file detection, hidden endpoint enumeration, and JavaScript route analysis
---

# Web Content Discovery

Web content discovery identifies hidden files, directories, endpoints, and functionality that are not linked from the public-facing application. Forgotten backup files, exposed admin panels, debug endpoints, and unprotected API routes are consistently among the highest-value findings in penetration tests. Systematic enumeration with intelligent wordlists and response analysis separates thorough testers from those who only test what they can see.

## Attack Surface

**Web Server File System**
- Default installation files, documentation, and sample applications
- Backup files created by editors, deployments, or administrators
- Configuration files with credentials, API keys, and database connection strings
- Version control artifacts (.git, .svn, .hg directories) exposed on production servers

**Application Routes**
- Unlinked admin panels, debug endpoints, and internal tools
- API versioning endpoints (v1, v2, v3) with deprecated but still active versions
- REST/GraphQL introspection endpoints exposing full API schemas
- WebSocket and Server-Sent Events endpoints not documented in public APIs

**Static Assets and Build Artifacts**
- JavaScript source maps (.map files) revealing original source code
- Webpack/build manifests listing all application modules and routes
- Compiled but unminified JavaScript containing route definitions and API calls
- Environment-specific configuration files (.env.production, config.staging.js)

**Infrastructure Artifacts**
- Cloud metadata endpoints (169.254.169.254) accessible through SSRF
- Health check and status endpoints (/health, /status, /ready, /metrics, /debug)
- Container orchestration endpoints (Kubernetes API, Docker socket)
- CI/CD artifacts: Jenkinsfile, .gitlab-ci.yml, .github/workflows exposed in webroot

## Key Vulnerabilities

### Directory and File Brute-Forcing

- `ffuf -w wordlist.txt -u https://target.com/FUZZ -mc 200,301,302,403 -fs 0`
- `gobuster dir -u https://target.com -w wordlist.txt -t 50 -x php,asp,aspx,jsp`
- `dirsearch -u https://target.com -e php,asp,html -t 30 --random-agent`
- Wordlist selection is critical: SecLists `raft-medium-directories.txt`, `common.txt`, Assetnote wordlists
- Recursive scanning: discover `/api/`, then brute-force `/api/FUZZ` for nested content
- Extension fuzzing: append `.php`, `.asp`, `.aspx`, `.jsp`, `.json`, `.xml`, `.yaml` to each word

### Backup and Temporary File Detection

- Common extensions: `.bak`, `.old`, `.orig`, `.save`, `.swp`, `.swo`, `~`, `.tmp`, `.temp`
- Editor artifacts: `.file.php.swp` (vim), `file.php~` (nano/emacs), `#file.php#` (emacs auto-save)
- Deployment backups: `file.php.bak`, `file.php.20240101`, `file.php.dist`, `file.php.sample`
- Archive files: `backup.zip`, `backup.tar.gz`, `db.sql`, `dump.sql`, `site.tar`, `www.zip`
- Configuration backups: `web.config.old`, `.htaccess.bak`, `wp-config.php.bak`, `settings.py.bak`
- `ffuf -w filenames.txt -u https://target.com/FUZZ -e .bak,.old,.swp,.tmp,.zip,.sql,.tar.gz`

### Version Control Exposure

- Git: `/.git/HEAD`, `/.git/config`, `/.git/logs/HEAD` - reconstruct with `git-dumper`
- SVN: `/.svn/entries`, `/.svn/wc.db` - extract with `svn-extractor`
- Mercurial: `/.hg/store/00manifest.i` - reconstruct repository history
- Full source code recovery including commit history, credentials in old commits, and internal documentation
- `python3 git-dumper.py https://target.com/.git/ output_dir`

### JavaScript Route and Endpoint Analysis

- Extract routes from bundled JavaScript: `cat app.js | grep -oP '["'"'"'](/[a-zA-Z0-9_/\-\.]+)["'"'"']'`
- Tools: `LinkFinder`, `JSParser`, `getJS`, `subjs` for automated JS endpoint extraction
- Webpack chunk analysis: `https://target.com/static/js/chunk-vendors.js.map` for source maps
- React/Angular/Vue route definitions often contain all application paths in client-side bundles
- GraphQL introspection: `{"query":"{__schema{types{name,fields{name}}}}"}` at `/graphql`
- API documentation endpoints: `/swagger.json`, `/openapi.json`, `/api-docs`, `/swagger-ui/`

### Response Fingerprinting and Filtering

- Filter by response size to eliminate generic error pages: `ffuf ... -fs 4242`
- Filter by word count, line count, or status code: `-fw 12 -fl 5 -mc 200,301`
- Calibrate filters by sending a known-invalid request first and noting the baseline response
- Use `-ac` (auto-calibrate) in ffuf for automatic false positive filtering
- Compare response headers: custom headers, cookies, and caching directives differ between real and default pages
- Hash response bodies to detect subtle content variations across similar-looking pages

### Hidden Parameter Discovery

- `arjun -u https://target.com/endpoint` for automatic parameter detection
- `paramspider -d target.com` for parameter mining from web archives
- `x8 -u https://target.com/endpoint -w params.txt` for high-speed parameter brute-forcing
- Test both GET and POST with different content types (form, JSON, XML)
- Common hidden params: `debug`, `test`, `admin`, `verbose`, `format`, `callback`, `redirect`, `next`

## Bypass Techniques

**403 Forbidden Bypass**
- Path normalization: `/admin/`, `/admin/.`, `/admin/..;/`, `//admin`, `/./admin`
- URL encoding: `/%61dmin`, `/admin%00`, `/admin%20`, `/admin%09`
- HTTP method override: `X-Original-URL: /admin`, `X-Rewrite-URL: /admin`
- Header injection: `X-Forwarded-For: 127.0.0.1`, `X-Custom-IP-Authorization: 127.0.0.1`
- Case variation: `/Admin`, `/ADMIN`, `/aDmIn` on case-insensitive servers
- Add trailing characters: `/admin.json`, `/admin;`, `/admin..;/`, `/admin/./`

**WAF Evasion**
- Slow scan rates with random delays: `ffuf --delay 100-500ms`
- Rotate User-Agent strings: `--header "User-Agent: Mozilla/5.0..."`
- Distribute scans across multiple source IPs or proxy chains
- Use less common HTTP methods (OPTIONS, PROPFIND) to probe path existence

**Content Type Manipulation**
- Request same path with different Accept headers to trigger alternative handlers
- Append extensions: `/api/users.json`, `/api/users.xml`, `/api/users.csv`
- Content negotiation may reveal different response formats or error handling paths

## Testing Methodology

1. **Baseline fingerprinting** - Identify web server, framework, and CMS via headers, error pages, and default files
2. **Wordlist selection** - Choose wordlists matching the identified technology stack (PHP, Java, .NET, Python)
3. **Initial sweep** - Run broad directory/file brute-force with common wordlists and relevant extensions
4. **Recursive discovery** - Brute-force discovered directories recursively to map nested content
5. **Backup file scan** - Test all discovered filenames with backup extensions (.bak, .old, .swp, ~)
6. **VCS detection** - Check for .git, .svn, .hg directories and attempt full reconstruction
7. **JavaScript analysis** - Download and analyze all JS files for routes, API endpoints, and secrets
8. **API discovery** - Probe for swagger/openapi docs, GraphQL introspection, and WADL/WSDL files
9. **Parameter fuzzing** - Test discovered endpoints for hidden parameters that unlock functionality
10. **403 bypass** - Attempt bypass techniques on all forbidden but discovered paths

## Validation

1. Confirm discovered content is genuinely accessible and not a false positive from generic error handling
2. Verify backup files contain actual application source code or configuration by inspecting content
3. Validate VCS reconstruction by checking out files and comparing with live application behavior
4. Confirm JavaScript-extracted endpoints are active by sending requests and analyzing responses
5. Document exact URLs, response codes, and content hashes for reproducibility

## False Positives

- Custom 404 pages returning 200 status codes with dynamic content
- WAFs or reverse proxies returning identical responses for all non-existent paths
- Soft 404s that include the requested path in the response body (inflating apparent uniqueness)
- Load balancer health check endpoints that respond 200 to any request path
- CDN edge nodes caching and serving stale responses for previously valid paths

## Impact

- Source code disclosure via backup files enabling white-box vulnerability analysis
- Credential exposure in configuration files (.env, web.config, wp-config.php, settings.py)
- Full repository reconstruction from exposed .git directories including commit history
- Administrative interface access enabling unauthorized system configuration changes
- API schema disclosure revealing undocumented endpoints and authentication bypass opportunities
- Debug endpoint access leaking stack traces, environment variables, and internal state

## Pro Tips

1. Build custom wordlists from the target application: spider the site, extract paths, and generate permutations
2. Use `cewl` to create target-specific wordlists from page content for parameter and directory fuzzing
3. Check Wayback Machine for historical paths: `waybackurls target.com | sort -u` reveals removed content
4. Source maps are goldmines: `/static/js/main.chunk.js.map` often contains complete React/Angular source
5. Monitor `robots.txt` and `sitemap.xml` for paths the application explicitly wants to keep hidden from crawlers
6. Test for common debug endpoints: `/debug`, `/trace`, `/actuator` (Spring Boot), `/__debug__` (Django)
7. Use `gau` (GetAllURLs) to aggregate URLs from multiple archive sources before targeted scanning
8. Run `nuclei -t exposures/` for automated detection of common misconfigurations and exposed files
9. Chain discovered information: credentials from config files may work on discovered admin panels
10. Always check `/robots.txt`, `/.well-known/`, `/sitemap.xml`, `/crossdomain.xml`, `/clientaccesspolicy.xml`

## Summary

Web content discovery succeeds when systematic enumeration is combined with technology-aware wordlists, intelligent response filtering, and thorough analysis of client-side code. The most impactful findings are often files and endpoints that developers forgot existed. Cover backup files, version control artifacts, JavaScript routes, and API documentation endpoints on every engagement.
