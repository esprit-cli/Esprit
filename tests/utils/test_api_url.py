from __future__ import annotations

from unittest.mock import patch

import pytest

from esprit.utils.api_url import get_esprit_api_base_url, is_trusted_runtime_url


def test_get_esprit_api_base_url_defaults_to_https_prod() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert get_esprit_api_base_url() == "https://esprit.dev/api/v1"


def test_get_esprit_api_base_url_rejects_non_https() -> None:
    with patch.dict("os.environ", {"ESPRIT_API_URL": "http://evil.example"}, clear=True):
        with pytest.raises(ValueError):
            get_esprit_api_base_url()


def test_get_esprit_api_base_url_allows_local_http_when_explicitly_enabled() -> None:
    with patch.dict(
        "os.environ",
        {
            "ESPRIT_API_URL": "http://127.0.0.1:9000/api/v1",
            "ESPRIT_ALLOW_INSECURE_LOCAL_API": "1",
        },
        clear=True,
    ):
        assert get_esprit_api_base_url() == "http://127.0.0.1:9000/api/v1"


def test_is_trusted_runtime_url_accepts_same_reg_domain() -> None:
    assert is_trusted_runtime_url(
        "https://api.esprit.dev/api/v1",
        "https://runtime.esprit.dev/sandbox/sbx-1",
    )


def test_is_trusted_runtime_url_rejects_unrelated_host() -> None:
    assert not is_trusted_runtime_url(
        "https://api.esprit.dev/api/v1",
        "https://runtime.attacker.dev/sandbox/sbx-1",
    )
