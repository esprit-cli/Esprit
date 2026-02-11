---
name: osint
description: Open-source intelligence gathering covering WHOIS, DNS history, Google dorking, Shodan/Censys, GitHub secret scanning, metadata extraction, and Wayback Machine
---

# OSINT

Open-source intelligence transforms publicly available data into actionable targeting information. OSINT performed before any active scanning reduces direct interaction with the target, lowers detection risk, and frequently reveals credentials, infrastructure details, and organizational structure that bypass technical controls entirely. The best reconnaissance is the kind the target never detects.

## Attack Surface

**Domain and Network Registration**
- WHOIS records: registrant names, emails, phone numbers, organization details
- Historical WHOIS snapshots revealing previous owners, contacts, and infrastructure changes
- ASN ownership and IP range allocations mapping organizational network boundaries
- DNS registration patterns across related domains and subsidiaries

**Public Infrastructure Data**
- Shodan, Censys, and BinaryEdge indexes of internet-facing services and banners
- Certificate transparency logs revealing hostnames and organizational relationships
- Historical DNS records showing infrastructure migrations and abandoned assets
- BGP routing data exposing peering relationships and network topology

**Code and Document Repositories**
- GitHub, GitLab, Bitbucket public repositories with hardcoded secrets and internal documentation
- Paste sites (Pastebin, GitHub Gists) containing leaked credentials and configuration dumps
- Public document metadata (PDF, DOCX, XLSX) embedding author names, software versions, and file paths
- Docker Hub images with embedded credentials, internal hostnames, and configuration files

**Search Engine and Web Archive Data**
- Google dorking to find indexed sensitive pages, login portals, and exposed files
- Wayback Machine snapshots preserving removed content, old configurations, and historical endpoints
- Cached pages revealing content the target has since deleted or restricted
- Social media and professional network profiles mapping organizational structure and technology choices

## Key Vulnerabilities

### WHOIS and Domain Intelligence

- `whois target.com` for registrant details, nameservers, creation/expiration dates
- Privacy-protected WHOIS: check historical records via DomainTools, WhoisXMLAPI, SecurityTrails
- Reverse WHOIS: find all domains registered by the same organization/email/phone
- `amass intel -whois -d target.com` discovers related domains through registrant correlation
- Domain expiration monitoring: expired domains can be registered for phishing or subdomain takeover
- Registrar transfer history may reveal organizational changes and acquisition targets

### DNS History and Analysis

- SecurityTrails historical DNS: `curl -s "https://api.securitytrails.com/v1/history/target.com/dns/a" -H "APIKEY: key"`
- Historical records reveal old IP addresses, previous hosting providers, and decommissioned infrastructure
- MX record history shows email provider migrations (on-premise to O365/Google Workspace)
- NS record changes indicate DNS provider migrations and potential transition-state vulnerabilities
- SPF/DMARC/DKIM records reveal authorized mail senders and internal mail infrastructure
- `dnstwist -r target.com` finds typosquatting domains used for phishing campaigns against the target

### Google Dorking

- **File discovery**: `site:target.com filetype:pdf OR filetype:xlsx OR filetype:docx OR filetype:sql`
- **Login pages**: `site:target.com inurl:login OR inurl:admin OR inurl:portal OR inurl:signin`
- **Directory listings**: `site:target.com intitle:"Index of" OR intitle:"Directory listing"`
- **Exposed configs**: `site:target.com filetype:env OR filetype:xml OR filetype:conf OR filetype:cfg`
- **Error pages**: `site:target.com intext:"error" OR intext:"exception" OR intext:"stack trace"`
- **Sensitive paths**: `site:target.com inurl:backup OR inurl:dump OR inurl:export OR inurl:temp`
- **Cloud storage**: `site:s3.amazonaws.com "target" OR site:blob.core.windows.net "target"`
- **Paste leaks**: `site:pastebin.com "target.com" OR site:paste.ee "target.com"`
- Use Google Cache (`cache:target.com/page`) to access recently removed content

### Shodan and Censys Reconnaissance

- `shodan search "hostname:target.com"` for all indexed services and banners
- `shodan search "ssl.cert.subject.CN:target.com"` for TLS certificate-based host discovery
- `shodan search "org:'Target Organization'"` for all services owned by the organization
- `censys search "services.tls.certificates.leaf.names:target.com"` for certificate-based enumeration
- Filter by vulnerable products: `shodan search "apache/2.4.49" "hostname:target.com"` (known CVE versions)
- Shodan monitor: set up continuous alerts for new services appearing on target IP ranges
- `shodan host 1.2.3.4` for detailed service information on a specific IP
- Export results for bulk analysis: `shodan download results "org:'Target'"` then `shodan parse results.json.gz`

### GitHub and Code Repository Secret Scanning

- `trufflehog github --org target-org` scans all organization repositories for secrets
- `gitleaks detect --source /path/to/repo` for local repository credential scanning
- GitHub search operators: `org:target "password" OR "api_key" OR "secret" OR "token"`
- `org:target filename:.env` or `org:target filename:config extension:yml password`
- Search commit history: secrets may be removed from current code but exist in prior commits
- `git log --all -p --diff-filter=D -- "*.env"` in cloned repos to find deleted secret files
- Docker Hub: `docker pull target/app && docker history target/app` reveals build-time secrets in layers
- Check GitHub Actions workflows for hardcoded secrets and overly permissive OIDC configurations
- `gitleaks` or `trufflehog` for real-time monitoring of GitHub commits containing secrets

### Document Metadata Extraction

- `exiftool document.pdf` extracts author names, software versions, creation tools, and timestamps
- FOCA (Windows) for bulk metadata extraction and analysis across multiple document types
- Metadata reveals: internal usernames, Active Directory names, internal file paths, printer names
- Software version information from metadata guides targeted exploitation (old Office/Acrobat versions)
- GPS coordinates in images from corporate social media posts revealing office locations
- `metagoofil -d target.com -t pdf,doc,xls,ppt -o output_dir` downloads and analyzes public documents
- Strip metadata before sharing: `exiftool -all= document.pdf` (useful for OPSEC, not recon)

### Wayback Machine and Web Archives

- `waybackurls target.com | sort -u` retrieves all archived URLs for the target domain
- `gau target.com` aggregates URLs from Wayback, OTX, Common Crawl, and VirusTotal
- Historical snapshots reveal removed pages, old admin panels, and previous technology stacks
- API endpoints visible in archived JavaScript files that may still be active on the current server
- `web.archive.org/web/*/target.com/robots.txt` shows historically disallowed paths
- Compare archived pages to current versions to identify recently added security controls
- `waymore -i target.com -mode U` for comprehensive URL extraction from multiple archive sources
- Old sitemaps and directory listings preserved in archives reveal content the target has since hidden

## Bypass Techniques

**WHOIS Privacy Circumvention**
- Cross-reference historical WHOIS with current DNS/cert data for registrant identification
- Certificate transparency SANs often reveal organizational relationships hidden by WHOIS privacy
- Reverse IP lookups and shared hosting analysis to identify organizational patterns

**Search Engine Restrictions**
- Use multiple search engines: Google, Bing, Yandex, DuckDuckGo have different indexes
- Regional Google instances (google.co.jp, google.de) may return different cached results
- Use search engine APIs for automated bulk queries without CAPTCHA interference
- Supplement with specialized search: PublicWWW (source code search), grep.app (Git search)

**Rate Limit Management**
- Distribute Shodan/Censys queries using API keys with higher quotas
- Cache results locally to avoid repeated queries for the same data
- Use bulk export features instead of individual queries where available

## Testing Methodology

1. **Domain intelligence** - Run WHOIS, reverse WHOIS, and historical DNS queries on the target domain
2. **Search engine reconnaissance** - Execute Google dork queries across file types, login pages, and exposed directories
3. **Infrastructure indexing** - Query Shodan/Censys for all services associated with the target organization and IP ranges
4. **Certificate transparency** - Mine CT logs for all certificates issued to the target domain and subdomains
5. **Code repository scanning** - Search GitHub/GitLab for organization repositories, secrets, and internal documentation
6. **Document harvesting** - Download public documents and extract metadata for usernames, software, and paths
7. **Web archive analysis** - Query Wayback Machine for historical URLs, removed content, and old configurations
8. **Social media profiling** - Map organizational structure, technology preferences, and key personnel from LinkedIn/Twitter
9. **Data breach correlation** - Check Have I Been Pwned and breach databases for compromised organizational credentials
10. **Intelligence synthesis** - Correlate all findings into a target profile mapping infrastructure, people, and potential attack paths

## Validation

1. Cross-reference OSINT findings through multiple independent sources to confirm accuracy
2. Verify discovered credentials against public breach databases before assuming they are current
3. Confirm historical infrastructure details by checking if old IP addresses still resolve or respond
4. Validate organizational relationships found through WHOIS by checking corporate filings and press releases
5. Document all sources, timestamps, and confidence levels for each intelligence finding

## False Positives

- Outdated WHOIS data retained after domain transfers or organizational restructuring
- Shared hosting IP addresses incorrectly attributed to the target organization
- Recycled IP addresses with Shodan data from previous tenants, not the current target
- Common metadata fields (e.g., "admin", "user") that are default values, not real usernames
- Archived web content that has been intentionally replaced and no longer reflects current state
- Breach data associated with personal accounts, not organizational credentials

## Impact

- Credential discovery enabling unauthorized access without any active exploitation
- Organizational structure mapping facilitating targeted social engineering and phishing
- Infrastructure intelligence guiding active scanning and reducing detection surface
- Historical vulnerability exposure revealing unpatched systems and abandoned assets
- Supply chain intelligence identifying third-party relationships and shared infrastructure risks
- Technology stack profiling enabling targeted exploit development before first contact

## Pro Tips

1. Always start with OSINT before any active scanning; passive intelligence is undetectable
2. Set up monitoring for new certificate issuances, code commits, and Shodan entries for ongoing intelligence
3. Correlate email addresses across LinkedIn, GitHub, breach databases, and WHOIS for comprehensive profiling
4. Use `theHarvester -d target.com -b all` as a quick aggregator across multiple OSINT sources
5. Check GitHub Issues and Pull Requests, not just code; internal discussions often leak architecture details
6. Docker Hub image layers frequently contain environment variables, API keys, and internal hostnames
7. Build a timeline: domain registration, infrastructure changes, employee transitions reveal organizational patterns
8. Use Shodan CLI filters to find specific vulnerable services: `shodan search "product:Apache httpd" "vuln:CVE-2021-41773"`
9. Pastebin alternatives (dpaste, PrivateBin, paste.ee) are often overlooked; automate monitoring with `psbdmp`
10. Keep OSINT findings organized in a structured format (Maltego, SpiderFoot, or custom databases) for correlation

## Summary

OSINT is the force multiplier for penetration testing. Comprehensive passive intelligence gathering maps the target's infrastructure, people, technology, and potential credentials before any active scanning begins. The most effective reconnaissance combines domain intelligence, code repository scanning, search engine exploitation, and web archive analysis to build a complete target profile. Start passive, stay organized, and correlate findings across sources for maximum impact.
