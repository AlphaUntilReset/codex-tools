"""Exercise the real urllib response handling without sockets or real credentials."""

from email.message import Message
import io
import json
from pathlib import Path
import socket
import sys
import urllib.request
import urllib.response

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from codex_pet.bridge import plan_usage as usage


@pytest.fixture
def transport(monkeypatch):
    requests = []
    response = {"status": 200, "body": b"", "location": None}
    build_opener = urllib.request.build_opener

    class SyntheticHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            requests.append(request)
            headers = Message()
            if response["location"]:
                headers["Location"] = response["location"]
            result = urllib.response.addinfourl(
                io.BytesIO(response["body"]), headers, request.full_url, response["status"])
            result.msg = "Synthetic response"
            return result

    def no_socket(*args, **kwargs):
        raise AssertionError("This test must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers:
                        build_opener(urllib.request.ProxyHandler({}), SyntheticHTTPS(), *handlers))
    monkeypatch.setattr(usage, "_load_auth", lambda _: ("synthetic-test-token", "synthetic-account"))
    return response, requests


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", [
    "https://unrelated.example.test/usage",
    "http://unrelated.example.test/usage",
    "https://chatgpt.com/another-path",
])
def test_redirects_do_not_forward_sign_in_headers(transport, tmp_path, status, location):
    response, requests = transport
    response.update(status=status, location=location)
    result = usage.fetch_usage_snapshot(tmp_path, clock=lambda: 1000.0)
    assert result.state is usage.UsageState.UNREACHABLE
    assert result.fetched_at == 1000.0
    assert len(requests) == 1
    assert requests[0].full_url == usage.USAGE_URL
    assert requests[0].get_header("Authorization") == "Bearer synthetic-test-token"
    assert requests[0].get_header("Chatgpt-account-id") == "synthetic-account"


def test_successful_fixed_endpoint_query_still_parses(transport, tmp_path):
    response, requests = transport
    response["body"] = json.dumps({"rate_limit": {"primary_window": {
        "used_percent": 25, "reset_after_seconds": 3600,
        "limit_window_seconds": 7200}}}).encode("utf-8")
    result = usage.fetch_usage_snapshot(tmp_path, clock=lambda: 1000.0)
    assert result.state is usage.UsageState.OK
    assert result.remaining_percent == 75
    assert result.reset_at == 4600.0
    assert len(requests) == 1


@pytest.mark.parametrize("status,expected", [
    (401, usage.UsageState.AUTH_EXPIRED),
    (403, usage.UsageState.UNREACHABLE),
    (500, usage.UsageState.UNREACHABLE),
])
def test_http_errors_keep_existing_state(transport, tmp_path, status, expected):
    response, requests = transport
    response.update(status=status, body=b"Synthetic failure")
    assert usage.fetch_usage_snapshot(tmp_path).state is expected
    assert len(requests) == 1
