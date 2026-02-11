---
name: kubernetes
description: Kubernetes security testing covering RBAC misconfiguration, container escapes, secrets exposure, network policy gaps, and service account token abuse
---

# Kubernetes Security

Kubernetes orchestrates containerized workloads across clusters and introduces a distinct security model centered on RBAC, namespaces, service accounts, and network policies. The API server is the single control point for all cluster operations, making it the primary target. Misconfigurations in RBAC bindings, pod security, secrets management, and network segmentation are pervasive because Kubernetes defaults favor functionality over security. Container escapes from privileged pods or host-mounted volumes transform a single compromised workload into full node and potentially cluster-wide compromise.

## Architecture

A Kubernetes cluster consists of a control plane (API server, etcd, scheduler, controller manager) and worker nodes running the kubelet and container runtime. The API server authenticates requests via client certificates, bearer tokens, or OIDC, then authorizes them through RBAC. Service accounts provide pod-level identity with auto-mounted JWT tokens. etcd stores all cluster state including secrets in base64 encoding. The kubelet on each node manages pod lifecycle and exposes an API on ports 10250 (authenticated) and historically 10255 (read-only, deprecated). Network policies are enforced by CNI plugins but are not applied by default, meaning all pods can communicate with all other pods across all namespaces.

Key architectural elements for security testers:
- API server is the single point of control; all kubectl commands translate to REST API calls
- Admission controllers (mutating and validating webhooks) enforce policy at resource creation time
- Pod Security Standards (PSS) replace the deprecated PodSecurityPolicy for pod-level restrictions
- Container runtimes (containerd, CRI-O) provide the actual isolation boundary between pods and the host

## Attack Surface

- API server: exposed on public internet, weak authentication, anonymous access enabled
- RBAC: overly permissive ClusterRoleBindings, wildcard verbs/resources, privilege escalation paths
- Pods: privileged containers, hostPID/hostNetwork/hostIPC, sensitive host path mounts
- Secrets: mounted as environment variables or volumes, stored unencrypted in etcd
- Service accounts: default token auto-mounting, excessive RBAC bindings on default accounts
- Kubelet: unauthenticated API access, container exec/attach capabilities
- Network: flat network by default, no network policies, service mesh bypass
- Container images: running as root, vulnerable base images, embedded credentials
- etcd: unauthenticated access if exposed, contains all cluster secrets
- Admission controllers: missing or misconfigured pod security standards, webhook bypass
- Ingress controllers: TLS termination issues, path traversal, annotation injection
- Custom Resource Definitions (CRDs): custom controllers with elevated permissions, unvalidated input

## High-Value Targets

- Cluster admin ClusterRoleBinding or equivalent RBAC permissions
- etcd datastore containing all secrets, configmaps, and cluster state
- Service account tokens with broad permissions (especially in kube-system namespace)
- Node-level access providing kubelet credentials and access to all pod secrets on that node
- Cloud provider credentials accessible via node metadata service (IMDS) from within pods
- CI/CD service accounts with deployment permissions across multiple namespaces
- Kubernetes dashboard or monitoring tools with cluster-wide read access
- kubeconfig files on developer machines, CI/CD systems, or within pods
- Helm release secrets containing chart values with database passwords and API keys
- Tiller (Helm v2) service with cluster-admin if still present in legacy clusters
- Cloud provider credentials in node instance metadata accessible from pods without network policies

## Reconnaissance

- `kubectl cluster-info` to identify API server endpoint and cluster services
- `kubectl get namespaces` to enumerate all namespaces in the cluster
- `kubectl auth can-i --list` to discover permissions of the current service account
- `kubectl get pods --all-namespaces` to map all running workloads
- `kubectl get secrets --all-namespaces` to identify accessible secrets
- `kubectl get clusterrolebindings -o wide` to map privileged RBAC assignments
- `kubectl get networkpolicies --all-namespaces` to identify network segmentation gaps
- From within a pod: `cat /var/run/secrets/kubernetes.io/serviceaccount/token` for the SA token
- API server endpoint from pod: `https://kubernetes.default.svc.cluster.local`
- `kubectl get nodes -o wide` to identify node IPs, OS versions, and container runtimes
- `kubectl api-resources --verbs=list` to discover all available resource types for enumeration
- `kubectl get events --all-namespaces --sort-by='.lastTimestamp'` to understand recent cluster activity

## Key Vulnerabilities

### RBAC Misconfiguration

- Wildcard permissions: `verbs: ["*"]` and `resources: ["*"]` in ClusterRoles grant full access
- `system:masters` group membership provides irrevocable cluster-admin equivalent access
- Default service accounts with non-trivial bindings: check `default` SA in every namespace
- `escalate` verb on roles allows creating RBAC bindings with more permissions than the creator has
- `bind` verb on clusterroles allows binding any existing ClusterRole to any subject
- `impersonate` verb enables acting as any user, group, or service account
- Namespace-scoped RoleBindings referencing ClusterRoles inherit the ClusterRole permissions within that namespace
- `kubectl auth can-i create pods --as=system:serviceaccount:NAMESPACE:SA_NAME` to test escalation
- Common pattern: CI/CD service account with `create deployments` + `create pods` = arbitrary code execution
- Check for `list secrets` permission: enables reading all secret data across the namespace
- `kubectl get clusterroles -o json | jq '.items[] | select(.rules[].verbs[] == "*")'` to find wildcard roles

### Container Escape

- Privileged pods (`privileged: true`): full access to host devices, kernel capabilities, and `/dev`
- Escape via: `nsenter --target 1 --mount --uts --ipc --net --pid -- /bin/bash` from privileged container
- `hostPID: true`: view all host processes, attach to host process namespaces
- `hostNetwork: true`: access host network stack, bind to host ports, reach node IMDS
- `hostIPC: true`: access host shared memory segments
- Host path mounts: `/` or `/etc` mounted into container allows host filesystem manipulation
- Write to `/etc/cron.d/` via host mount for persistent root code execution on the node
- Write to `/var/run/docker.sock` mount: create containers on the host with `docker run --privileged`
- `SYS_PTRACE` capability: allows process injection into other containers on the same node
- `SYS_ADMIN` capability: allows mounting filesystems and abusing cgroups for escape
- Writable `/proc/sys` or `/sys` enables kernel parameter modification and potential escape
- CVE-based escapes: runc (CVE-2019-5736), containerd (CVE-2020-15257) when runtime is unpatched

### Secrets Exposure

- Secrets mounted as environment variables are visible in: `/proc/self/environ`, `kubectl describe pod`, container runtime inspection
- Secrets mounted as volumes are accessible at the specified mount path within the container
- etcd stores secrets base64-encoded but not encrypted unless EncryptionConfiguration is enabled
- `kubectl get secret SECRET -o jsonpath='{.data.password}' | base64 -d` to decode secrets
- Helm release secrets (`sh.helm.release.v1.*`) contain entire chart values including passwords
- Third-party secrets operators (External Secrets, Sealed Secrets) may cache plaintext in cluster
- Service account tokens are JWTs that can be decoded to reveal namespace, SA name, and expiry
- Container runtime socket access allows inspecting environment variables of all containers on the node
- Pod spec visible to anyone with `get pods` permission, exposing env var secret references
- ConfigMaps frequently contain sensitive data that should be in secrets
- Sealed Secrets or External Secrets Operator may leave plaintext cached in the cluster

### Network Policy Gaps

- Default Kubernetes networking is flat: every pod can reach every other pod across all namespaces
- No default network policies means no segmentation until explicitly configured
- Egress policies often missing: pods can reach external services, metadata APIs, and the internet
- DNS policy gaps: pods can query kube-dns for service discovery across namespaces
- NetworkPolicy only applies to the CNI plugin: some plugins do not enforce policies (e.g., Flannel without Calico)
- `kubectl get networkpolicies -A` returning empty results means zero network segmentation
- Service mesh (Istio, Linkerd) mTLS does not replace network policies; they address different threats
- Pod-to-node communication often unfiltered: pods can reach kubelet API at node IP:10250
- Metadata service access: without network policy blocking 169.254.169.254, all pods can access cloud IMDS
- NodePort services expose directly on all nodes, bypassing ingress-level security controls
- LoadBalancer services create cloud load balancers that may bypass cloud-level firewall rules
- DNS exfiltration: pods can resolve external DNS, enabling data tunneling through DNS queries

### Service Account Token Abuse

- Auto-mounted tokens at `/var/run/secrets/kubernetes.io/serviceaccount/token` are present in every pod by default
- Token format: legacy (non-expiring, non-audience-bound) vs bound (time-limited, audience-restricted)
- Use token: `curl -k -H "Authorization: Bearer $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" https://kubernetes.default.svc/api/v1/namespaces`
- `automountServiceAccountToken: false` in pod spec or service account disables auto-mounting
- kube-system service accounts often have cluster-wide permissions for controllers
- Token projection allows requesting tokens for specific audiences but legacy tokens may still work
- Compromised pod with powerful SA can create privileged pods, read secrets, or modify deployments
- Node bootstrap tokens (`system:bootstrappers` group) may allow new node registration
- Look for ServiceAccount annotations indicating cloud provider workload identity bindings
- Token request API (`kubectl create token SA_NAME`) generates short-lived tokens for testing

## Bypass Techniques

- Pod Security Standards (PSS) in warn/audit mode do not block pod creation, only log violations
- PodSecurityPolicy (deprecated): if not applied to the namespace or service account, pods bypass restrictions
- Admission webhook bypass: if webhook is configured with `failurePolicy: Ignore`, downtime in webhook allows bypass
- RBAC aggregation: custom ClusterRoles with aggregation labels automatically gain permissions from matching roles
- `ephemeralContainers` sub-resource may not be restricted by pod security controls
- Network policies with incorrect label selectors create gaps in coverage
- `kubectl debug` can attach privileged ephemeral containers if the user has patch rights on pods
- Namespace labels for PSS can be modified if the attacker has namespace update permissions
- Init containers may have different security contexts than the main containers
- Sidecar injection (Istio/Linkerd) may introduce containers with capabilities not in the original pod spec

## Tooling

- **kubectl**: primary tool for all Kubernetes interaction and enumeration
- **kubeaudit**: automated auditing of Kubernetes clusters for security misconfigurations
- **kube-bench**: CIS Kubernetes Benchmark compliance checker
- **kube-hunter**: Kubernetes penetration testing tool for active exploitation
- **Peirates**: Kubernetes post-exploitation toolset for privilege escalation
- **kubeletctl**: direct kubelet API interaction for node-level exploitation
- **KubiScan**: scan for risky RBAC permissions and service accounts
- **Trivy**: container image vulnerability scanning and Kubernetes misconfiguration detection
- **CDK (Container escape Detection Kit)**: container escape exploit toolkit
- **Deepce**: Docker and container enumeration and escape tool

## Testing Methodology

1. **Access assessment** - Determine initial access level: external API access, pod-level access, or node-level access
2. **RBAC enumeration** - Map all ClusterRoles, ClusterRoleBindings, Roles, and RoleBindings; identify wildcard and escalation permissions
3. **Service account audit** - Check every namespace for non-default SA permissions and auto-mounted tokens
4. **Pod security review** - Identify privileged containers, host mounts, dangerous capabilities, and containers running as root
5. **Secrets assessment** - Enumerate accessible secrets, check for env var exposure, and test etcd encryption
6. **Network policy analysis** - Verify network policy coverage across all namespaces; test pod-to-pod and pod-to-external communication
7. **Container escape testing** - From compromised pod, attempt escape via privileges, host mounts, or kernel exploits
8. **Node-level exploitation** - After node access, extract kubelet credentials, pod secrets, and container runtime data
9. **Cloud integration** - Test metadata service access from pods, check workload identity configuration
10. **Persistence mechanisms** - Identify admission webhook, CronJob, or DaemonSet vectors for maintaining access

## Validation Requirements

1. Demonstrate RBAC escalation by showing the full path from initial SA to elevated permissions
2. Prove container escape by executing commands on the host node from within a container
3. Confirm secrets access by decoding retrieved secret data (redact actual credentials)
4. Validate network policy gaps by demonstrating cross-namespace communication or metadata access
5. Show service account token abuse by making authenticated API calls with extracted tokens
6. Document the blast radius: what resources can be accessed from the compromised position

## False Positives

- Privileged pods for legitimate system components (CNI plugins, log collectors, monitoring agents)
- Host mounts for specific directories needed by the workload (not root filesystem mounts)
- ClusterRoleBindings for infrastructure controllers that require broad permissions by design
- Auto-mounted SA tokens in pods that do not make any API server calls
- Missing network policies in development/testing clusters not intended for production
- Service mesh sidecar containers with elevated capabilities required for traffic interception

## Impact

- Full cluster compromise through RBAC escalation to cluster-admin
- Node takeover via container escape from privileged pods or host mount abuse
- Mass secret exfiltration from etcd or Kubernetes API across all namespaces
- Lateral movement to cloud infrastructure through metadata service access from pods
- Supply chain attacks by modifying deployments, injecting sidecar containers, or poisoning images
- Denial of service through resource quota exhaustion, pod eviction, or control plane disruption
- Persistent backdoor access via CronJobs, DaemonSets, mutating admission webhooks, or static pods

## Pro Tips

1. Always run `kubectl auth can-i --list` first to understand your exact permissions in the current context
2. Check for the `system:discovery` and `system:basic-user` ClusterRoleBindings; they reveal cluster info to all authenticated users
3. `kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.securityContext}{"\n"}{end}'` to quickly find privileged pods
4. etcd access is game over: `ETCDCTL_API=3 etcdctl get /registry/secrets --prefix --keys-only` lists all secrets
5. From a pod with `hostPID`, use `nsenter -t 1 -m -u -i -n -p -- cat /etc/kubernetes/pki/apiserver-kubelet-client.key` for kubelet certs
6. CronJobs and DaemonSets provide persistence that survives pod deletion and node restarts
7. Check for `system:anonymous` ClusterRoleBindings: unauthenticated users may have cluster read access
8. Kubernetes audit logs record all API calls; check if they are enabled and where they are sent before noisy operations

## Summary

Kubernetes security fundamentally depends on the interplay between RBAC, pod security, network segmentation, and secrets management. The most devastating attack paths chain initial pod compromise with privileged container escape or RBAC escalation to achieve node-level or cluster-wide control. Default configurations are insecure by design: service account tokens are auto-mounted, network policies are absent, and secrets are stored unencrypted. Effective Kubernetes penetration testing requires thinking in terms of the pod-node-cluster-cloud hierarchy and systematically testing each boundary for misconfigurations that allow upward movement.
