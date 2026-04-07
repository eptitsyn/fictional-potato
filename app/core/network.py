from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_DOCKER_ENV_FILE = Path("/.dockerenv")
_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}
_HTTP_SCHEMES = {"http", "https"}


def running_in_docker() -> bool:
    return _DOCKER_ENV_FILE.exists()


def _format_host(hostname: str) -> str:
    return f"[{hostname}]" if ":" in hostname else hostname


def validate_http_base_url(base_url: str) -> str:
    normalized = base_url.strip()
    if not normalized:
        raise ValueError("Base URL is required")

    parts = urlsplit(normalized)
    if parts.scheme.lower() not in _HTTP_SCHEMES:
        raise ValueError("Base URL must use http or https")
    if not parts.hostname:
        raise ValueError("Base URL must include a host")
    if parts.username or parts.password:
        raise ValueError("Base URL must not embed credentials")
    if parts.query or parts.fragment:
        raise ValueError("Base URL must not include query parameters or fragments")

    path = parts.path.rstrip("/")
    netloc = _format_host(parts.hostname.lower())
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


def resolve_endpoint_base_url(base_url: str) -> str:
    """
    Make host-local endpoint URLs reachable from inside Docker containers.

    When the app runs in Docker, `localhost` points at the container itself.
    Replace it with `host.docker.internal` so endpoints like a local Ollama
    instance remain reachable from the app and worker containers.
    """
    normalized = validate_http_base_url(base_url)
    if not running_in_docker():
        return normalized

    parts = urlsplit(normalized)
    if not parts.hostname or parts.hostname not in _LOCALHOST_HOSTS:
        return normalized

    netloc = parts.netloc
    if parts.hostname == "::1":
        netloc = netloc.replace("[::1]", "host.docker.internal")
    else:
        netloc = netloc.replace(parts.hostname, "host.docker.internal", 1)

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
