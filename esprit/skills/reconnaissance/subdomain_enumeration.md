---
name: subdomain-enumeration
description: Subdomain enumeration techniques covering DNS zone transfers, brute-force, certificate transparency, passive DNS, and virtual host discovery
---

# Subdomain Enumeration

Subdomain enumeration is the foundational reconnaissance phase that maps an organization's external attack surface. Discovering forgotten, misconfigured, or development subdomains frequently exposes unpatched services, default credentials, and internal tooling. A thorough enumeration combines active DNS probing, passive intelligence sources, and virtual host brute-forcing to build a complete picture before any exploitation begins.

## Attack Surface

**DNS Infrastructure**
- Authoritative nameservers, recursive resolvers, split-horizon DNS
- Zone transfers (AXFR/IXFR) on misconfigured primaries and secondaries
- NSEC/NSEC3 zone walking on DNSSEC-enabled domains
- Dynamic DNS entries and stale CNAME/A records pointing to decommissioned infrastructure

**Certificate Transparency**
- CT logs indexed by crt.sh, Censys, Google Transparency Report
- Pre-certificates exposing internal hostnames before services go live
- Wildcard certificates that hint at naming conventions

**Passive Intelligence**
- Search engine caches, Wayback Machine snapshots, CommonCrawl datasets
- Threat intelligence feeds, VirusTotal passive DNS, SecurityTrails historical records
- SPF/DMARC/DKIM records embedding mail infrastructure hostnames
- Public code repositories leaking internal hostnames in configs, CI/CD pipelines, and documentation

**Cloud and CDN**
- S3/GCS/Azure blob naming conventions mirroring subdomain patterns
- CloudFront, Fastly, and Akamai CNAME chains revealing origin hostnames
- Dangling DNS records pointing to unclaimed cloud resources (subdomain takeover surface)

## Key Vulnerabilities

### DNS Zone Transfer Exposure

- Test AXFR against every discovered nameserver: `dig axfr @ns1.target.com target.com`
- Secondary nameservers are often more permissive than primaries
- IXFR (incremental) may leak recent additions even when full AXFR is restricted
- Zone files expose internal naming conventions, mail servers, and service records (SRV)
- Tools: `dig`, `host`, `dnsrecon -t axfr`, `fierce`

### Brute-Force Enumeration

- Wordlist-driven resolution using curated lists: SecLists `subdomains-top1million-110000.txt`, Assetnote wordlists
- Recursive brute-forcing: discover `dev.target.com`, then brute-force `*.dev.target.com`
- Tools: `subfinder`, `amass enum -brute`, `puredns`, `massdns`, `shuffledns`
- DNS resolver selection matters: use trusted public resolvers (1.1.1.1, 8.8.8.8) and dedicated resolver lists to avoid poisoned results
- Rate limiting: respect resolver limits to avoid bans; distribute across resolver pools
- Command: `puredns bruteforce wordlist.txt target.com --resolvers resolvers.txt`

### Certificate Transparency Mining

- Query crt.sh: `curl -s "https://crt.sh/?q=%25.target.com&output=json" | jq -r '.[].name_value' | sort -u`
- Censys certificates API for historical and expired certificate entries
- Extract SANs (Subject Alternative Names) which often list multiple subdomains per certificate
- Monitor CT logs in real-time using `certstream` for newly issued certificates
- Pre-certificates reveal hostnames before DNS records are even created

### Passive DNS Aggregation

- Combine results from multiple passive sources for coverage: SecurityTrails, PassiveTotal, VirusTotal, Robtex
- `amass enum -passive -d target.com` aggregates 40+ passive data sources
- `subfinder -d target.com -all` pulls from Shodan, Censys, VirusTotal, and more
- Historical DNS records reveal decommissioned but still-resolving subdomains
- Reverse DNS (PTR) lookups on known IP ranges: `dnsrecon -r 10.0.0.0/24`

### Virtual Host Discovery

- Brute-force Host headers against known IPs to find vhosts not in DNS
- `ffuf -w wordlist.txt -u http://TARGET_IP -H "Host: FUZZ.target.com" -fs 0`
- Filter by response size, status code, or word count to isolate valid vhosts
- TLS SNI probing: connect with different SNI values and compare certificate responses
- Reverse IP lookup services (HackerTarget, ViewDNS) to find co-hosted domains

### Wildcard Detection and Handling

- Detect wildcards by querying random nonexistent subdomains: `dig randomgarbage12345.target.com`
- If wildcard responds, filter results by comparing against the wildcard response (IP, size, content hash)
- `puredns` and `massdns` have built-in wildcard detection and filtering
- Some wildcards are conditional (different responses per subdomain prefix) requiring deeper analysis
- Wildcard CNAME records pointing to CDNs may mask actual backend variations

## Bypass Techniques

**Rate Limit Evasion**
- Distribute queries across large resolver pools (use 50+ resolvers)
- Introduce random jitter between queries to avoid pattern detection
- Use TCP fallback when UDP-based rate limiting is detected

**DNS Firewall Circumvention**
- Query alternative record types: TXT, MX, SRV, CNAME may resolve when A is blocked
- Use DNS-over-HTTPS (DoH) or DNS-over-TLS (DoT) to bypass network-level DNS filtering
- Query authoritative nameservers directly, bypassing recursive resolver filtering

**Passive Source Maximization**
- Chain multiple passive tools: `subfinder` + `amass` + `assetfinder` + `findomain`, then deduplicate
- Use API keys for premium data sources (SecurityTrails, Censys, Shodan, BinaryEdge, C99)
- Scrape search engines with `theHarvester` for indexed subdomains missed by DNS tools

**Subdomain Takeover Detection**
- Identify dangling CNAMEs pointing to unclaimed services (GitHub Pages, Heroku, S3, Azure, Shopify)
- Tools: `subjack`, `nuclei -t takeovers/`, `can-i-take-over-xyz` reference list
- Verify by attempting to claim the resource and serving a proof-of-concept page

## Testing Methodology

1. **Passive collection** - Run `subfinder`, `amass passive`, crt.sh queries, and search engine scraping in parallel
2. **Deduplicate and normalize** - Merge all results, strip wildcards, lowercase, remove duplicates
3. **DNS resolution** - Resolve all candidates with `massdns` or `puredns` against a validated resolver list
4. **Wildcard filtering** - Detect and remove wildcard-inflated results
5. **Active brute-force** - Run wordlist-based brute-force on the root domain and any discovered subdomains with depth
6. **Virtual host probing** - Test discovered IPs for additional vhosts not present in DNS
7. **Subdomain takeover check** - Scan all CNAME records for dangling references to claimable services
8. **Port scanning** - Feed validated subdomains into port/service scanning for the next recon phase
9. **Continuous monitoring** - Set up `subfinder`/`amass` on cron or use `notify` for new subdomain alerts

## Validation

1. Confirm discovered subdomains resolve to live IP addresses via multiple independent resolvers
2. Verify zone transfer results by cross-referencing with passive DNS sources
3. Validate virtual hosts by confirming unique content served per Host header value
4. Test subdomain takeover candidates by verifying the dangling CNAME and confirming claimability
5. Document all data sources used and timestamps for reproducibility

## False Positives

- Wildcard DNS records returning valid responses for any queried subdomain
- CDN/load balancer IPs shared across unrelated customers appearing in reverse lookups
- Expired or cached passive DNS entries that no longer resolve
- Parked or default pages served by hosting providers regardless of the requested hostname
- DNS rebinding or round-robin responses producing inconsistent resolution results

## Impact

- Expanded attack surface revealing forgotten development, staging, and admin panels
- Subdomain takeover enabling phishing, cookie theft, and CSP bypass on the parent domain
- Discovery of internal services exposed to the internet without proper access controls
- Identification of shadow IT and unapproved cloud deployments outside security monitoring
- Intelligence on naming conventions enabling targeted brute-force of deeper infrastructure

## Pro Tips

1. Always combine at least three passive sources with active brute-forcing; no single tool has complete coverage
2. Run recursive brute-force on discovered subdomains (e.g., brute-force `*.dev.target.com` after finding `dev.target.com`)
3. Use permutation tools like `dnsgen` or `altdns` to generate variations from known subdomains
4. Monitor CT logs continuously; new certificates are issued for subdomains before they appear in other sources
5. Check ASN ownership with `amass intel -asn AS12345` to discover IP ranges and reverse-resolve additional hostnames
6. Feed discovered subdomains directly into `httpx` for HTTP probing: `cat subs.txt | httpx -title -tech-detect -status-code`
7. Maintain a per-target resolver list; some internal DNS servers leak more than public resolvers
8. Export all findings to structured formats (JSON/CSV) for integration with downstream scanning tools
9. Use `dnsx` to extract CNAME chains and identify cloud provider patterns for takeover assessment
10. Prioritize subdomains with interesting names (admin, staging, dev, internal, vpn, api) for deeper investigation

## Summary

Effective subdomain enumeration requires layering passive intelligence, active DNS probing, certificate transparency mining, and virtual host discovery. The goal is complete attack surface mapping before any exploitation begins. Wildcard handling, resolver selection, and continuous monitoring separate thorough reconnaissance from superficial scans.
