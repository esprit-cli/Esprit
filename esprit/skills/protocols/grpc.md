---
name: grpc
description: gRPC security testing covering service reflection, metadata auth bypass, and protobuf field injection
---

# gRPC

Security testing for gRPC services. Focus on service reflection exploitation, authentication bypass via metadata manipulation, protobuf field injection, streaming abuse, and health check information disclosure.

## Attack Surface

**Transports**
- HTTP/2 with TLS (default production), HTTP/2 cleartext (h2c) in development/internal
- gRPC-Web (HTTP/1.1 + HTTP/2 via Envoy proxy), Unix domain sockets for local IPC

**Service Definition**
- Protobuf service contracts (`.proto` files), server reflection (`grpc.reflection.v1` and legacy `grpc.reflection.v1alpha`)
- Health checking protocol (`grpc.health.v1`), Channelz for runtime introspection

**RPC Patterns**
- Unary (request-response), server streaming, client streaming, bidirectional streaming

**Authentication**
- TLS client certificates (mTLS), token-based via `authorization` metadata key
- Custom metadata keys for API keys/tenant IDs, per-RPC vs channel credentials

## Reconnaissance

**Service Discovery**
```bash
# Check if reflection is enabled
grpcurl -plaintext target:50051 list
grpcurl -plaintext target:50051 describe my.service.v1.UserService

# Common ports: 50051, 443, 8443, 9090, 9443
```

**Without Reflection**
- Recover `.proto` files from client applications, mobile APKs, WASM bundles
- Decompile protobuf descriptors embedded in binaries
- Probe known paths: `/grpc.health.v1.Health/Check`, `/grpc.reflection.v1.ServerReflection/ServerReflectionInfo` (or legacy `/grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo`)
- Use `protoc --decode_raw` on captured binary payloads to infer field structure

**Fingerprinting**
```bash
# Health check (often unauthenticated)
grpcurl -plaintext target:50051 grpc.health.v1.Health/Check
# Channelz (debug introspection, sometimes exposed)
grpcurl -plaintext target:50051 grpc.channelz.v1.Channelz/GetServers
```

**Endpoint Mapping**
Enumerate all services and methods via reflection. Catalog request/response message types, field numbers, types (especially `bytes`, `string`, `repeated`), and `oneof` fields. Identify methods that accept user-controllable IDs.

## Key Vulnerabilities

### Service Reflection Exploitation

Reflection exposes the full service contract including internal services, admin RPCs, and message structures never intended for external consumers.

```bash
# List all services including internal ones
grpcurl -plaintext target:50051 list
# Often reveals: AdminService, DebugService, MigrationService

# Invoke admin methods
grpcurl -plaintext -d '{"user_id": "target_user"}' \
  target:50051 my.service.v1.AdminService/PromoteToAdmin
```

Internal services are frequently unprotected because they were designed for service-to-service communication behind a network boundary.

### Metadata Authentication Bypass

**Missing Auth on Specific Methods**
```bash
# Test without credentials
grpcurl -plaintext -d '{}' target:50051 my.service.v1.UserService/ListUsers
# Test with empty/expired tokens
grpcurl -plaintext -H 'authorization: Bearer ' \
  -d '{"id":"1"}' target:50051 my.service.v1.UserService/GetUser
```

**Metadata Key Confusion**
```bash
# Case sensitivity: Authorization vs authorization vs AUTHORIZATION
# Binary metadata suffix (-bin keys are base64 decoded)
-H 'auth-token-bin: base64encodedvalue'
# Custom tenant/role injection
-H 'x-tenant-id: foreign_tenant'
-H 'x-user-role: admin'
-H 'x-internal-service: true'
```

gRPC interceptors may check `authorization` but pass through custom metadata keys that downstream handlers trust implicitly.

### Protobuf Field Injection

**Unknown Field Preservation**
Protobuf v3 preserves unknown fields by default. If a client sends fields not in its schema view but present in the server's newer schema, the server deserializes them.
```bash
# If GetUserRequest has field 1 (id), inject field 99 (maybe role)
echo '089901' | xxd -r -p | grpcurl -plaintext -d @ target:50051 \
  my.service.v1.UserService/GetUser
```

**Field Number Collision**
When services evolve, reserved field numbers may still be parsed. Inject values for deprecated but still-deserialized fields.

**Default Value Bypass**
Protobuf v3 does not distinguish "field not set" from "field set to default value" (0, "", false):
```bash
grpcurl -plaintext -d '{"user_id": "1", "is_admin": true}' \
  target:50051 my.service.v1.UserService/UpdateUser
```

### Streaming Abuse

**Server Streaming Enumeration**
```bash
grpcurl -plaintext -d '{"filter": "*"}' \
  target:50051 my.service.v1.EventService/StreamEvents
```
Verify that stream filters enforce per-user authorization. Wildcard or empty filters may return all events across tenants.

**Client Streaming Flooding**
Send unbounded messages in a client stream to exhaust server memory or trigger OOM. Test whether the server enforces message count or size limits per stream.

**Bidirectional Stream Hijacking**
Test whether the server validates each inbound message independently or assumes the stream context established by the first message.

### Health Check and Debug Exposure

```bash
# Health check may reveal service names and dependency status
grpcurl -plaintext -d '{"service":"my.service.v1.PaymentService"}' \
  target:50051 grpc.health.v1.Health/Check
# Channelz exposes internal topology, IPs, connection counts
grpcurl -plaintext target:50051 grpc.channelz.v1.Channelz/GetTopChannels
```

### IDOR via Message Fields

```bash
grpcurl -plaintext -d '{"user_id": "foreign_user_id"}' \
  target:50051 my.service.v1.UserService/GetUserProfile

# Enumerate sequential IDs
for i in $(seq 1 100); do
  grpcurl -plaintext -d "{\"id\": \"$i\"}" \
    target:50051 my.service.v1.OrderService/GetOrder
done
```

Test with UUIDs from other users, tenant IDs from other organizations, and numeric IDs outside expected ranges.

## Bypass Techniques

**Transport Switching**
```bash
# h2c (cleartext HTTP/2) may bypass TLS-terminating proxies
grpcurl -plaintext target:50051 list

# gRPC-Web via HTTP/1.1 may have different auth middleware
curl -X POST https://target.com/my.service.v1.UserService/GetUser \
  -H 'Content-Type: application/grpc-web+proto' \
  -H 'X-Grpc-Web: 1' --data-binary @request.bin

# JSON transcoding via gRPC-Gateway
curl https://target.com/v1/users/123
```

Different transports often route through different middleware stacks with inconsistent authorization enforcement.

**Deadline/Timeout Manipulation**
Set extremely short deadlines to trigger timeout before auth check completes, or extremely long deadlines to hold server resources indefinitely.

**Interceptor Ordering Exploitation**
gRPC interceptors execute in chain order. If logging runs before auth, sensitive data may be logged from unauthenticated requests. If rate-limiting runs after auth, unauthenticated floods bypass rate limits.

## Testing Methodology

1. **Discovery** - Port scan for gRPC (50051, 443, 9090), test reflection, probe health check
2. **Schema extraction** - Use reflection or recovered `.proto` files to map all services and methods
3. **Auth matrix** - Test each method with no auth, expired auth, wrong-role auth, cross-tenant auth
4. **Field injection** - Send unknown fields, default values, type-confused values for each message type
5. **Streaming tests** - Verify per-message auth on streams, test unbounded client streams, wildcard filters
6. **Transport parity** - Compare auth enforcement across h2c, gRPC-Web, JSON transcoding
7. **Internal services** - Attempt to invoke admin/debug/migration services discovered via reflection
8. **Metadata injection** - Inject custom metadata keys that downstream services may trust

## Validation Requirements

- Reflection exposure: full service listing showing internal/admin services accessible externally
- Auth bypass: successful RPC invocation without valid credentials with response data
- IDOR: response data from foreign user/tenant with compared legitimate vs illegitimate request pairs
- Field injection: server-side effect from injected protobuf fields not in client schema
- Stream abuse: cross-tenant data received via server stream with permissive filter
- Transport parity: same RPC succeeds unauthenticated on one transport but requires auth on another

## False Positives

- Health check endpoints intentionally unauthenticated for load balancer probes
- Reflection enabled only in development/staging environments behind network controls
- Services that appear internal but are intentionally exposed for partner integrations
- gRPC status code `UNAUTHENTICATED` vs `PERMISSION_DENIED` may indicate partial enforcement rather than absence

## Impact

- **Reflection exposure**: Complete API surface disclosure enabling targeted attacks on internal services
- **Auth bypass**: Unauthorized data access and state mutation across the entire service
- **Field injection**: Privilege escalation via injected role/permission fields, data corruption
- **Stream abuse**: Mass data exfiltration, cross-tenant information disclosure, resource exhaustion

## Pro Tips

- Use `grpcurl` with `-v` flag to see full HTTP/2 headers including trailers with error details
- Compile custom `.proto` files with injected fields and use `protoc --encode` for binary payloads
- Use `grpcui` (GUI tool) for interactive exploration when reflection is available
- Capture gRPC traffic with Wireshark using HTTP/2 dissector; decode protobuf with `protobuf_search_paths`
- Check Envoy/Istio sidecar configs for gRPC-specific routing rules that may expose internal services
- Mobile apps often bundle `.proto` descriptors; extract with `apktool` and search for `descriptor.pb`
- Test `grpc-status-details-bin` trailer for verbose error messages leaking internal state

## Summary

gRPC security testing centers on four axes: reflection exposure revealing internal service topology, metadata-based authentication that can be bypassed through key manipulation or transport switching, protobuf field injection exploiting schema evolution and unknown field preservation, and streaming RPCs with insufficient per-message authorization. The binary protocol and strong typing create a false sense of security; the same IDOR, auth bypass, and injection vulnerabilities present in REST APIs exist in gRPC but require specialized tooling to exploit. Always test transport parity between native gRPC, gRPC-Web, and JSON transcoding endpoints.
