from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DOCKER_ENV_FILE = Path("/.dockerenv")
_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}
_HTTP_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_TEST_HOSTS = {"testserver"}

logger = logging.getLogger(__name__)

_ALLOWED_HTTP_ORIGINS: frozenset[str] = frozenset()
_HTTPX_GUARD_INSTALLED = False
_POLICY_LOCK = RLock()
_ORIGINAL_ASYNC_CLIENT_INIT = None
_ORIGINAL_SYNC_CLIENT_INIT = None
_ORIGINAL_ASYNC_CLIENT_SEND = None
_ORIGINAL_SYNC_CLIENT_SEND = None


class OutboundHTTPBlockedError(RuntimeError):
    pass


def running_in_docker() -> bool:
    return _DOCKER_ENV_FILE.exists()


def _format_host(hostname: str) -> str:
    return f"[{hostname}]" if ":" in hostname else hostname


def _canonical_origin(parts) -> str:
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if not scheme or not hostname:
        raise ValueError("URL must include scheme and host")

    origin = f"{scheme}://{_format_host(hostname)}"
    port = parts.port
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        origin = f"{origin}:{port}"
    return origin


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


def origin_for_url(url: str) -> str:
    return _canonical_origin(urlsplit(url))


def _iter_allowed_origins(base_urls: list[str], *, resolve_localhost: bool) -> set[str]:
    origins: set[str] = set()
    for base_url in base_urls:
        try:
            normalized = validate_http_base_url(base_url)
            origins.add(origin_for_url(normalized))
            if resolve_localhost:
                origins.add(origin_for_url(resolve_endpoint_base_url(normalized)))
        except ValueError:
            logger.warning("Skipping invalid configured base URL while building HTTP allowlist")
    return origins


def build_allowed_http_origins(
    git_server_urls: list[str], llm_endpoint_urls: list[str]
) -> frozenset[str]:
    origins = _iter_allowed_origins(git_server_urls, resolve_localhost=False)
    origins.update(_iter_allowed_origins(llm_endpoint_urls, resolve_localhost=True))
    return frozenset(origins)


def set_allowed_http_origins(origins: set[str] | frozenset[str]) -> frozenset[str]:
    global _ALLOWED_HTTP_ORIGINS
    frozen = frozenset(origins)
    with _POLICY_LOCK:
        _ALLOWED_HTTP_ORIGINS = frozen
    return frozen


def get_allowed_http_origins() -> frozenset[str]:
    with _POLICY_LOCK:
        return _ALLOWED_HTTP_ORIGINS


def assert_http_request_allowed(url: str | httpx.URL) -> None:
    parts = urlsplit(str(url))
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _HTTP_SCHEMES or not host:
        raise OutboundHTTPBlockedError(f"Blocked outbound request with unsupported URL: {url}")
    if host in _TEST_HOSTS:
        return

    origin = _canonical_origin(parts)
    if origin not in get_allowed_http_origins():
        raise OutboundHTTPBlockedError(
            f"Blocked outbound HTTP request to {origin}. "
            "Only configured git servers and LLM endpoints are allowed."
        )


def install_httpx_outbound_guard() -> None:
    global _HTTPX_GUARD_INSTALLED
    global _ORIGINAL_ASYNC_CLIENT_INIT, _ORIGINAL_SYNC_CLIENT_INIT
    global _ORIGINAL_ASYNC_CLIENT_SEND, _ORIGINAL_SYNC_CLIENT_SEND

    with _POLICY_LOCK:
        if _HTTPX_GUARD_INSTALLED:
            return

        _ORIGINAL_ASYNC_CLIENT_INIT = httpx.AsyncClient.__init__
        _ORIGINAL_SYNC_CLIENT_INIT = httpx.Client.__init__
        _ORIGINAL_ASYNC_CLIENT_SEND = httpx.AsyncClient.send
        _ORIGINAL_SYNC_CLIENT_SEND = httpx.Client.send

        def guarded_async_init(self, *args, **kwargs):
            kwargs.setdefault("trust_env", False)
            return _ORIGINAL_ASYNC_CLIENT_INIT(self, *args, **kwargs)

        def guarded_sync_init(self, *args, **kwargs):
            kwargs.setdefault("trust_env", False)
            return _ORIGINAL_SYNC_CLIENT_INIT(self, *args, **kwargs)

        async def guarded_async_send(self, request, *args, **kwargs):
            assert_http_request_allowed(request.url)
            return await _ORIGINAL_ASYNC_CLIENT_SEND(self, request, *args, **kwargs)

        def guarded_sync_send(self, request, *args, **kwargs):
            assert_http_request_allowed(request.url)
            return _ORIGINAL_SYNC_CLIENT_SEND(self, request, *args, **kwargs)

        httpx.AsyncClient.__init__ = guarded_async_init
        httpx.Client.__init__ = guarded_sync_init
        httpx.AsyncClient.send = guarded_async_send
        httpx.Client.send = guarded_sync_send
        _HTTPX_GUARD_INSTALLED = True


async def refresh_outbound_http_allowlist(db: AsyncSession) -> frozenset[str]:
    from sqlalchemy import select

    from app.models.git_server import GitServer
    from app.models.llm import LLMEndpoint

    git_server_urls = list((await db.execute(select(GitServer.base_url))).scalars().all())
    llm_endpoint_urls = list((await db.execute(select(LLMEndpoint.base_url))).scalars().all())
    return set_allowed_http_origins(build_allowed_http_origins(git_server_urls, llm_endpoint_urls))


async def ensure_outbound_http_policy(db: AsyncSession) -> frozenset[str]:
    install_httpx_outbound_guard()
    return await refresh_outbound_http_allowlist(db)


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
