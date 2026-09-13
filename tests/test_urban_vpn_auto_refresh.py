import time

import core.browser_manager as browser_manager


def _reset_token_cache(monkeypatch):
    monkeypatch.setattr(browser_manager, "_urban_cached_security_token", None)
    monkeypatch.setattr(browser_manager, "_urban_cached_security_token_expires_at", 0.0)


def test_anonymous_token_refresh_is_cached_for_parallel_workers(monkeypatch):
    _reset_token_cache(monkeypatch)
    calls = []

    def fake_request(url, token, method="GET", payload=None):
        calls.append((url, token, method, payload))
        if "/registrations/" in url:
            return {"value": "anonymous-auth"}
        return {
            "value": "anonymous-security",
            "expirationTime": int((time.time() + 3600) * 1000),
        }

    monkeypatch.setattr(browser_manager, "_urban_request_json", fake_request)

    assert browser_manager._refresh_urban_vpn_security_token() == "anonymous-security"
    assert browser_manager._refresh_urban_vpn_security_token() == "anonymous-security"
    assert len(calls) == 2


def test_proxy_resolution_auto_refreshes_without_extension_login(monkeypatch):
    _reset_token_cache(monkeypatch)
    monkeypatch.setattr(browser_manager, "find_urban_vpn_extension", lambda: None)

    def fake_request(url, token, method="GET", payload=None):
        if "/registrations/" in url:
            return {"value": "anonymous-auth"}
        if url.endswith("/security/tokens/accs"):
            return {
                "value": "anonymous-security",
                "expirationTime": int((time.time() + 3600) * 1000),
            }
        if url.endswith("/entrypoints/countries"):
            assert token == "anonymous-security"
            return {
                "countries": {
                    "elements": [{
                        "code": {"iso2": "US"},
                        "accessType": "ACCESSIBLE",
                        "servers": {
                            "elements": [{
                                "accessType": "ACCESSIBLE",
                                "premium": False,
                                "weight": 10,
                                "signature": "server-signature",
                                "address": {
                                    "primary": {"host": "proxy.example", "port": 8080}
                                },
                            }]
                        },
                    }]
                }
            }
        if url.endswith("/security/tokens/accs-proxy"):
            assert token == "anonymous-security"
            return {"value": "proxy-user"}
        raise AssertionError(url)

    monkeypatch.setattr(browser_manager, "_urban_request_json", fake_request)

    assert browser_manager.resolve_urban_vpn_proxy("US") == {
        "host": "proxy.example",
        "port": 8080,
        "username": "proxy-user",
        "password": "1",
        "country": "US",
    }
