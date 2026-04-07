import httpx
import pytest

from app.core import database
from app.core import network


def test_validate_http_base_url_normalizes_and_rejects_dangerous_parts():
    assert (
        network.validate_http_base_url(" HTTPS://GitLab.EXAMPLE.com/root/ ")
        == "https://gitlab.example.com/root"
    )

    with pytest.raises(ValueError, match="query parameters"):
        network.validate_http_base_url("https://gitlab.example.com?via=proxy")

    with pytest.raises(ValueError, match="embed credentials"):
        network.validate_http_base_url("https://user:pass@gitlab.example.com")


def test_build_allowed_http_origins_includes_docker_localhost_resolution(monkeypatch):
    monkeypatch.setattr(network, "running_in_docker", lambda: True)

    origins = network.build_allowed_http_origins(
        git_server_urls=["https://gitlab.example.com"],
        llm_endpoint_urls=["http://localhost:11434/v1"],
    )

    assert "https://gitlab.example.com" in origins
    assert "http://localhost:11434" in origins
    assert "http://host.docker.internal:11434" in origins


@pytest.mark.asyncio
async def test_httpx_guard_blocks_non_allowlisted_http_hosts():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": str(request.url)})

    network.install_httpx_outbound_guard()
    network.set_allowed_http_origins(
        frozenset({"https://gitlab.example.com", "https://llm.example.com"})
    )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        allowed = await client.get("https://gitlab.example.com/api/v4/projects")
        assert allowed.status_code == 200

        with pytest.raises(network.OutboundHTTPBlockedError, match="Only configured git servers"):
            await client.get("https://example.com/anything")


class _ScalarResult:
    def __init__(self, values: list[str]):
        self._values = values

    def all(self) -> list[str]:
        return self._values


class _ExecuteResult:
    def __init__(self, values: list[str]):
        self._values = values

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._values)


class _PolicyRefreshSession:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, statement):
        rendered = str(statement)
        self.calls.append(rendered)
        if "git_servers.base_url" in rendered:
            return _ExecuteResult(["https://gitlab.example.com"])
        if "llm_endpoints.base_url" in rendered:
            return _ExecuteResult(["http://localhost:11434/v1"])
        raise AssertionError(f"Unexpected statement: {rendered}")


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ForbiddenCallerSession:
    async def execute(self, statement):
        raise AssertionError(f"Caller session should not be used: {statement}")


@pytest.mark.asyncio
async def test_ensure_outbound_http_policy_uses_dedicated_session(monkeypatch):
    dedicated_session = _PolicyRefreshSession()
    monkeypatch.setattr(network, "running_in_docker", lambda: False)
    monkeypatch.setattr(
        database,
        "AsyncSessionLocal",
        lambda: _SessionContext(dedicated_session),
    )

    origins = await network.ensure_outbound_http_policy(_ForbiddenCallerSession())

    assert origins == frozenset(
        {
            "https://gitlab.example.com",
            "http://localhost:11434",
        }
    )
    assert len(dedicated_session.calls) == 2
