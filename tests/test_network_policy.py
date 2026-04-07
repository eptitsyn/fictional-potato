import httpx
import pytest

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
