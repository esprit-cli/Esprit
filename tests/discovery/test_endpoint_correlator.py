"""Tests for mobile endpoint correlation."""

from esprit.discovery.endpoint_correlator import (
    EndpointCorrelator,
    _normalize_url,
    _should_exclude_url,
)


class TestEndpointExtraction:
    def test_extract_api_urls(self):
        correlator = EndpointCorrelator()
        text = '''
        String BASE_URL = "https://api.example.com/v2/users";
        String OTHER = "https://api.example.com/v2/orders";
        '''
        endpoints = correlator.extract_endpoints_from_text(text, "ApiService.java")
        assert len(endpoints) == 2
        assert any("users" in e["url"] for e in endpoints)
        assert any("orders" in e["url"] for e in endpoints)

    def test_extract_deduplicates(self):
        correlator = EndpointCorrelator()
        text = '''
        "https://api.example.com/v2/users/123"
        "https://api.example.com/v2/users/456"
        "https://api.example.com/v2/users/789"
        '''
        endpoints = correlator.extract_endpoints_from_text(text)
        # All three normalize to users/{id}, so should dedupe to 1
        assert len(endpoints) == 1

    def test_excludes_sdk_domains(self):
        correlator = EndpointCorrelator()
        text = '''
        "https://api.example.com/v1/data"
        "https://www.googleapis.com/auth/token"
        "https://crashlytics.com/report"
        '''
        endpoints = correlator.extract_endpoints_from_text(text)
        assert len(endpoints) == 1
        assert "example.com" in endpoints[0]["url"]

    def test_excludes_static_assets(self):
        correlator = EndpointCorrelator()
        text = '''
        "https://cdn.example.com/logo.png"
        "https://api.example.com/v1/data"
        "https://cdn.example.com/styles.css"
        '''
        endpoints = correlator.extract_endpoints_from_text(text)
        assert len(endpoints) == 1
        assert "data" in endpoints[0]["url"]


class TestEndpointCorrelation:
    def test_find_untested_endpoints(self):
        correlator = EndpointCorrelator()
        correlator.extract_endpoints_from_text(
            '"https://api.example.com/v1/users" "https://api.example.com/v1/admin"'
        )
        correlator.register_observed_endpoint("GET", "api.example.com", "/v1/users")

        untested = correlator.find_untested_endpoints()
        assert len(untested) == 1
        assert "admin" in untested[0]["url"]

    def test_all_endpoints_tested(self):
        correlator = EndpointCorrelator()
        correlator.extract_endpoints_from_text(
            '"https://api.example.com/v1/users"'
        )
        correlator.register_observed_endpoint("GET", "api.example.com", "/v1/users")

        untested = correlator.find_untested_endpoints()
        assert len(untested) == 0

    def test_register_from_requests_data(self):
        correlator = EndpointCorrelator()
        correlator.extract_endpoints_from_text(
            '"https://api.example.com/v1/orders"'
        )
        requests_data = [
            {"method": "GET", "host": "api.example.com", "path": "/v1/orders"},
        ]
        count = correlator.register_observed_endpoints_from_requests(requests_data)
        assert count == 1

        untested = correlator.find_untested_endpoints()
        assert len(untested) == 0

    def test_generate_hypotheses_for_untested(self):
        correlator = EndpointCorrelator()
        correlator.extract_endpoints_from_text(
            '"https://api.example.com/v1/admin/config"',
            source_file="AdminService.java",
        )

        hypotheses = correlator.generate_hypotheses_for_untested()
        assert len(hypotheses) == 1
        assert hypotheses[0].vulnerability_class == "Untested Endpoint"
        assert hypotheses[0].novelty_score > 0.5
        assert "admin" in hypotheses[0].target


class TestNormalization:
    def test_normalize_url_strips_ids(self):
        assert _normalize_url("https://api.example.com/users/123") == "api.example.com/users/{id}"

    def test_normalize_url_strips_uuids(self):
        result = _normalize_url(
            "https://api.example.com/users/550e8400-e29b-41d4-a716-446655440000"
        )
        assert result == "api.example.com/users/{id}"

    def test_normalize_url_lowercases(self):
        assert _normalize_url("https://API.Example.COM/Users") == "api.example.com/users"


class TestUrlExclusion:
    def test_exclude_googleapis(self):
        assert _should_exclude_url("https://www.googleapis.com/auth/token")

    def test_exclude_firebase(self):
        assert _should_exclude_url("https://myapp.firebaseio.com/data")

    def test_exclude_png(self):
        assert _should_exclude_url("https://cdn.example.com/logo.png")

    def test_include_api(self):
        assert not _should_exclude_url("https://api.example.com/v1/users")
