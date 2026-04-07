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


def test_resolve_endpoint_base_url_rewrites_localhost_inside_docker(monkeypatch):
    monkeypatch.setattr(network, "running_in_docker", lambda: True)

    resolved = network.resolve_endpoint_base_url("http://localhost:11434/v1")

    assert resolved == "http://host.docker.internal:11434/v1"


def test_resolve_endpoint_base_url_keeps_remote_hosts_unchanged(monkeypatch):
    monkeypatch.setattr(network, "running_in_docker", lambda: True)

    resolved = network.resolve_endpoint_base_url("https://llm.example.com/v1")

    assert resolved == "https://llm.example.com/v1"


def test_resolve_endpoint_base_url_keeps_localhost_outside_docker(monkeypatch):
    monkeypatch.setattr(network, "running_in_docker", lambda: False)

    resolved = network.resolve_endpoint_base_url("http://localhost:11434/v1")

    assert resolved == "http://localhost:11434/v1"
