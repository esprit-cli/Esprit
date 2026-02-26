"""Tests for scope enforcement."""

from esprit.discovery.scope_guard import ScopeGuard


class TestScopeGuard:
    def test_no_scope_allows_everything(self):
        guard = ScopeGuard(mode="block")
        result = guard.check_url("https://anything.com/api/test")
        assert result.allowed
        assert result.reason == "no_scope_defined"

    def test_register_web_targets(self):
        guard = ScopeGuard(mode="block")
        guard.register_targets([
            {
                "type": "web_application",
                "details": {"target_url": "https://example.com/app"},
            }
        ])
        assert "example.com" in guard.allowed_hosts

    def test_register_ip_targets(self):
        guard = ScopeGuard(mode="block")
        guard.register_targets([
            {
                "type": "ip_address",
                "details": {"target_ip": "192.168.1.1"},
            }
        ])
        assert "192.168.1.1" in guard.allowed_hosts

    def test_in_scope_allowed(self):
        guard = ScopeGuard(mode="block")
        guard.add_allowed_host("example.com")
        result = guard.check_url("https://example.com/api/users")
        assert result.allowed
        assert result.reason == "host_in_scope"

    def test_subdomain_allowed(self):
        guard = ScopeGuard(mode="block")
        guard.add_allowed_host("example.com")
        result = guard.check_url("https://api.example.com/v1/users")
        assert result.allowed
        assert result.reason == "subdomain_in_scope"

    def test_out_of_scope_blocked(self):
        guard = ScopeGuard(mode="block")
        guard.add_allowed_host("example.com")
        result = guard.check_url("https://evil.com/api/test")
        assert not result.allowed
        assert result.reason == "out_of_scope"
        assert "evil.com" in result.message

    def test_out_of_scope_warned(self):
        guard = ScopeGuard(mode="warn")
        guard.add_allowed_host("example.com")
        result = guard.check_url("https://evil.com/api/test")
        assert result.allowed  # warn mode still allows
        assert result.reason == "out_of_scope_warned"

    def test_check_send_request(self):
        guard = ScopeGuard(mode="block")
        guard.add_allowed_host("example.com")

        result = guard.check_request_args(
            "send_request", {"url": "https://example.com/api/test", "method": "GET"}
        )
        assert result.allowed

        result = guard.check_request_args(
            "send_request", {"url": "https://evil.com/api/test", "method": "GET"}
        )
        assert not result.allowed

    def test_repeat_request_always_allowed(self):
        guard = ScopeGuard(mode="block")
        guard.add_allowed_host("example.com")
        result = guard.check_request_args(
            "repeat_request", {"request_id": "req_1"}
        )
        assert result.allowed

    def test_add_hosts_from_proxy(self):
        guard = ScopeGuard(mode="block")
        count = guard.add_allowed_hosts_from_proxy([
            {"host": "api.example.com", "path": "/v1/users"},
            {"host": "cdn.example.com", "path": "/assets/logo.png"},
        ])
        assert count == 2
        assert "api.example.com" in guard.allowed_hosts

    def test_invalid_url(self):
        guard = ScopeGuard(mode="block")
        guard.add_allowed_host("example.com")
        result = guard.check_url("not-a-url")
        assert not result.allowed
        assert result.reason == "invalid_url"

    def test_scope_check_result_bool(self):
        guard = ScopeGuard(mode="block")
        guard.add_allowed_host("example.com")

        assert guard.check_url("https://example.com/api")
        assert not guard.check_url("https://evil.com/api")
