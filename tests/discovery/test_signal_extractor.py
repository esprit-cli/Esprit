"""Tests for signal extraction from tool results."""

from esprit.discovery.models import AnomalyType
from esprit.discovery.signal_extractor import SignalExtractor


class TestProxySignals:
    def test_extract_status_code_anomaly(self):
        extractor = SignalExtractor()
        result = {
            "requests": [
                {
                    "id": "req_1",
                    "method": "GET",
                    "host": "example.com",
                    "path": "/api/admin",
                    "response": {"statusCode": 403, "roundtripTime": 100},
                }
            ]
        }
        anomalies = extractor.extract_from_tool_result("list_requests", {}, result)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == AnomalyType.status_code
        assert "403" in anomalies[0].description

    def test_extract_timing_anomaly(self):
        extractor = SignalExtractor()
        result = {
            "requests": [
                {
                    "id": "req_2",
                    "method": "POST",
                    "host": "example.com",
                    "path": "/api/search",
                    "response": {"statusCode": 200, "roundtripTime": 8000},
                }
            ]
        }
        anomalies = extractor.extract_from_tool_result("list_requests", {}, result)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == AnomalyType.timing

    def test_no_anomaly_for_normal_request(self):
        extractor = SignalExtractor()
        result = {
            "requests": [
                {
                    "id": "req_3",
                    "method": "GET",
                    "host": "example.com",
                    "path": "/index.html",
                    "response": {"statusCode": 200, "roundtripTime": 50},
                }
            ]
        }
        anomalies = extractor.extract_from_tool_result("list_requests", {}, result)
        assert len(anomalies) == 0

    def test_extract_error_leak_from_view_request(self):
        extractor = SignalExtractor()
        result = {
            "body": "Internal Server Error\nTraceback (most recent call last):\n  File..."
        }
        anomalies = extractor.extract_from_tool_result(
            "view_request", {"request_id": "req_5"}, result
        )
        assert any(a.anomaly_type == AnomalyType.error_leak for a in anomalies)

    def test_extract_injection_signal_from_view_request(self):
        extractor = SignalExtractor()
        result = {
            "body": "You have an error in your SQL syntax near 'OR 1=1'"
        }
        anomalies = extractor.extract_from_tool_result(
            "view_request", {"request_id": "req_6"}, result
        )
        assert any(a.anomaly_type == AnomalyType.injection_signal for a in anomalies)

    def test_extract_from_send_request_500(self):
        extractor = SignalExtractor()
        result = {"status_code": 500, "id": "req_7", "body": "error"}
        args = {"method": "POST", "url": "https://example.com/api/login"}
        anomalies = extractor.extract_from_tool_result("send_request", args, result)
        assert any(a.anomaly_type == AnomalyType.status_code for a in anomalies)


class TestTerminalSignals:
    def test_extract_error_leak_from_terminal(self):
        extractor = SignalExtractor()
        result = {
            "content": "Exception in thread main: java.lang.NullPointerException\n  at com.app.Main(Main.java:42)"
        }
        anomalies = extractor.extract_from_tool_result(
            "terminal_execute", {"command": "curl http://target/api"}, result
        )
        assert any(a.anomaly_type == AnomalyType.error_leak for a in anomalies)

    def test_extract_endpoint_discovery(self):
        extractor = SignalExtractor()
        result = {
            "content": "Found: https://example.com/api/v2/users/admin\nFound: https://example.com/api/v2/config"
        }
        anomalies = extractor.extract_from_tool_result(
            "terminal_execute", {"command": "katana -u http://example.com"}, result
        )
        assert any(a.anomaly_type == AnomalyType.unexpected_data for a in anomalies)

    def test_no_signal_from_normal_output(self):
        extractor = SignalExtractor()
        result = {"content": "scan complete. 0 issues found."}
        anomalies = extractor.extract_from_tool_result(
            "terminal_execute", {"command": "ls"}, result
        )
        assert len(anomalies) == 0


class TestBrowserSignals:
    def test_extract_console_errors(self):
        extractor = SignalExtractor()
        result = {
            "url": "https://example.com/app",
            "console_logs": [
                {"type": "error", "text": "Uncaught TypeError: Cannot read property 'id'"},
                {"type": "log", "text": "App loaded"},
            ],
        }
        anomalies = extractor.extract_from_tool_result(
            "browser_action", {"action": "goto"}, result
        )
        assert any(a.anomaly_type == AnomalyType.error_leak for a in anomalies)

    def test_extract_xss_signal_from_page_source(self):
        extractor = SignalExtractor()
        result = {
            "url": "https://example.com/search?q=test",
            "source": '<div><script>alert(1)</script></div>',
        }
        anomalies = extractor.extract_from_tool_result(
            "browser_action", {"action": "view_source"}, result
        )
        assert any(a.anomaly_type == AnomalyType.injection_signal for a in anomalies)


class TestUnknownTool:
    def test_unknown_tool_returns_empty(self):
        extractor = SignalExtractor()
        anomalies = extractor.extract_from_tool_result("unknown_tool", {}, {"data": "test"})
        assert anomalies == []

    def test_non_dict_result_returns_empty(self):
        extractor = SignalExtractor()
        anomalies = extractor.extract_from_tool_result("list_requests", {}, None)
        assert anomalies == []
