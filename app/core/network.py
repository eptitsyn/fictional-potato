from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


_DOCKER_ENV_FILE = Path("/.dockerenv")
_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}


def running_in_docker() -> bool:
    return _DOCKER_ENV_FILE.exists()


def resolve_endpoint_base_url(base_url: str) -> str:
    """
    Make host-local endpoint URLs reachable from inside Docker containers.

    When the app runs in Docker, `localhost` points at the container itself.
    Replace it with `host.docker.internal` so endpoints like a local Ollama
    instance remain reachable from the app and worker containers.
    """
    if not running_in_docker():
        return base_url

    parts = urlsplit(base_url)
    if not parts.hostname or parts.hostname not in _LOCALHOST_HOSTS:
        return base_url

    netloc = parts.netloc
    if parts.hostname == "::1":
        netloc = netloc.replace("[::1]", "host.docker.internal")
    else:
        netloc = netloc.replace(parts.hostname, "host.docker.internal", 1)

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
