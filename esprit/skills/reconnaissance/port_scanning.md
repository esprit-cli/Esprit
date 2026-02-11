---
name: port-scanning
description: Port scanning and service enumeration techniques covering SYN/connect/UDP scans, version detection, OS fingerprinting, and firewall evasion
---

# Port Scanning

Port scanning identifies open network services, determines software versions, and maps firewall rules to establish the exploitable surface of a target. Effective scanning balances speed, stealth, and accuracy. Understanding scan types, timing, and evasion techniques is essential for both perimeter assessment and internal network pivoting.

## Attack Surface

**Network Layers**
- TCP services: web servers, databases, SSH, RDP, SMB, mail, DNS, custom application ports
- UDP services: DNS (53), SNMP (161/162), TFTP (69), NTP (123), DHCP, SIP, IPsec/IKE (500)
- SCTP services: diameter (3868), SS7/SIGTRAN in telecom environments

**Service Exposure Patterns**
- Default ports for known services (80/443, 22, 3389, 3306, 5432, 27017)
- Non-standard ports used to obscure services (SSH on 2222, web on 8080/8443)
- High ports (49152-65535) for ephemeral services, reverse shells, and C2 channels
- IPv6-only services missed by IPv4-only scans

**Infrastructure Context**
- Cloud security groups and NACLs filtering at the provider level
- Host-based firewalls (iptables, Windows Firewall, pf) with stateful inspection
- IDS/IPS systems (Snort, Suricata, Palo Alto) monitoring for scan signatures
- Load balancers and reverse proxies masking backend port exposure

## Key Vulnerabilities

### TCP SYN Scanning

- Half-open scan (default nmap): sends SYN, reads SYN/ACK or RST, never completes handshake
- `nmap -sS -p- target` for full 65535-port sweep
- Fastest reliable TCP scan; requires raw socket privileges (root/admin)
- SYN/ACK = open, RST = closed, no response = filtered
- Stateful firewalls may still log half-open connections; not truly invisible

### TCP Connect Scanning

- Full three-way handshake via OS socket API: `nmap -sT target`
- No root required; works through SOCKS proxies and pivots
- Slower and noisier than SYN; connection logged by target application
- Useful when raw sockets are unavailable (unprivileged shells, proxychains)
- `proxychains nmap -sT -Pn -n -p80,443,8080 target`

### UDP Scanning

- `nmap -sU --top-ports 200 target` (full UDP scan is extremely slow)
- Open ports may not respond; closed ports return ICMP port unreachable
- Combine with version detection: `nmap -sU -sV --version-intensity 2 -p53,161,500 target`
- Critical for discovering SNMP (community strings), DNS (zone transfer), NTP (amplification), TFTP
- Send protocol-specific probes to elicit responses: `nmap -sU --script snmp-info -p161 target`

### Version and Service Detection

- `nmap -sV --version-intensity 5 target` sends probes to identify exact software and version
- Banner grabbing: `nmap -sV --script banner -p22,80 target`
- SSL/TLS certificate inspection reveals software, hostnames, and organization info
- `nmap --script ssl-cert,ssl-enum-ciphers -p443 target`
- HTTP-specific: `nmap --script http-headers,http-title,http-server-header -p80,443,8080 target`

### OS Fingerprinting

- Active: `nmap -O target` analyzes TCP/IP stack behavior (window size, TTL, DF bit, options)
- Combine with version detection: `nmap -A target` (equivalent to `-sV -O --script default --traceroute`)
- Passive: `p0f` monitors traffic without sending probes
- TTL heuristics: Linux ~64, Windows ~128, Cisco/Solaris ~255
- Useful for identifying OS-specific exploit paths and default configurations

### High-Speed Scanning

- `masscan -p1-65535 --rate 10000 target/24` for massive network sweeps
- `naabu -host target -p - -rate 5000` with automatic service detection integration
- `rustscan -a target --ulimit 5000` for fast initial port discovery, then pipes to nmap
- Workflow: fast scanner for port discovery, then targeted nmap `-sV` on open ports only
- `masscan -p1-65535 10.0.0.0/8 --rate 100000 -oG masscan.out && nmap -sV -p$(ports) targets`

## Bypass Techniques

**Firewall Evasion**
- IP fragmentation: `nmap -f target` splits probes into 8-byte fragments
- MTU control: `nmap --mtu 16 target` for custom fragment sizes
- Idle/zombie scan: `nmap -sI zombie_host target` spoofs scan source using predictable IPID
- Source port manipulation: `nmap --source-port 53 target` (firewalls often allow DNS source port)
- FIN/NULL/XMAS scans: `nmap -sF/-sN/-sX target` exploit stateless firewall gaps

**Timing and Stealth**
- Timing templates: `-T0` (paranoid, 5min between probes) through `-T5` (insane, no delay)
- Custom timing: `--min-rate 1 --max-rate 10 --scan-delay 5s` for IDS evasion
- Randomize target order: `--randomize-hosts` to avoid sequential sweep detection
- Spread scans over days/weeks for red team engagements to stay below alert thresholds

**Decoy and Spoofing**
- Decoy scans: `nmap -D RND:10 target` mixes real scan with 10 random decoy source IPs
- Specific decoys: `nmap -D decoy1,decoy2,ME target` (ME marks your real IP position)
- MAC spoofing: `nmap --spoof-mac 0` (random MAC, only works on same L2 segment)
- IPv6 scanning: many firewalls have weaker IPv6 rules; `nmap -6 target`

**IDS/IPS Evasion**
- Append random data: `nmap --data-length 50 target` changes packet signature
- Bad checksums: `nmap --badsum target` (valid packets get responses; IDS may ignore bad checksums)
- TTL manipulation: set TTL so packets reach target but expire before IDS sensor
- Protocol-specific evasion: HTTP probes through `--script-args http.useragent="Mozilla/5.0..."`

## Testing Methodology

1. **Host discovery** - Determine live hosts: `nmap -sn -PE -PP -PM -PS22,80,443 -PA80,443 target/24`
2. **Fast port sweep** - Use `masscan` or `naabu` for full-range TCP port discovery across all live hosts
3. **Targeted TCP scan** - Run nmap `-sS -sV` on discovered open ports for version detection
4. **UDP scan** - Scan top 200 UDP ports on critical hosts: `nmap -sU --top-ports 200 -sV target`
5. **OS detection** - Run `-O` on hosts with multiple open ports for reliable fingerprinting
6. **Script scanning** - Apply relevant NSE scripts: `nmap --script vuln,safe,default -p<ports> target`
7. **IPv6 check** - Scan IPv6 addresses if dual-stack is suspected: `nmap -6 target`
8. **Result correlation** - Merge scan data, identify service versions, and map to known CVEs
9. **Re-scan filtered ports** - Retry filtered ports with evasion techniques (fragmentation, source port 53)

## Validation

1. Confirm open ports by establishing full TCP connections or receiving valid service banners
2. Cross-validate scan results from multiple tools (nmap + masscan + naabu) to catch discrepancies
3. Verify version detection accuracy by manually connecting and inspecting banners (`nc`, `openssl s_client`)
4. Test filtered port determination by comparing responses with and without evasion techniques
5. Document scan parameters, timing, and source IP for reproducibility

## False Positives

- Filtered ports misidentified as open due to transparent proxies or load balancers responding on all ports
- IDS/IPS resets (RST packets) mimicking closed ports on actually open services
- Rate-limiting causing inconsistent responses across multiple scan passes
- SYN cookies on the target responding with SYN/ACK regardless of port state
- Cloud provider health checks or WAFs responding to probes with generic responses
- NAT/PAT devices translating ports and masking true backend topology

## Impact

- Complete service inventory enabling targeted vulnerability research and exploit selection
- Discovery of unnecessary exposed services (database ports, admin interfaces, debug endpoints)
- Identification of outdated software versions with known CVEs through banner analysis
- Mapping of firewall rules and network segmentation gaps for lateral movement planning
- Detection of shadow services, backdoors, and unauthorized listeners on non-standard ports

## Pro Tips

1. Always scan all 65535 TCP ports; critical services often hide on non-standard ports
2. Use a two-phase approach: fast discovery (masscan/naabu) then deep inspection (nmap -sV) on open ports only
3. Save raw output in all formats: `nmap -oA scan_results` produces normal, XML, and grepable output simultaneously
4. Parse nmap XML with `xmlstarlet` or `python-libnmap` for programmatic analysis and reporting
5. For internal networks, ARP scan first: `nmap -sn -PR 192.168.1.0/24` is faster and more reliable than ICMP
6. Combine with `aquatone` or `gowitness` to screenshot all discovered HTTP services automatically
7. Use `nmap --script-updatedb` after adding custom NSE scripts for specialized service detection
8. Run scans from multiple source IPs/VPNs to detect source-based filtering rules
9. Monitor your own traffic with `tcpdump` or Wireshark to verify evasion techniques are working as intended
10. Schedule periodic re-scans during engagements; services may start/stop outside business hours

## Summary

Port scanning is the bridge between target discovery and exploitation. Combining fast sweeps with targeted deep scans, covering both TCP and UDP, and applying evasion techniques against defended networks produces the complete service inventory needed for effective penetration testing. Always scan all ports, verify results from multiple tools, and document everything for reproducibility.
