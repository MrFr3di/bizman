from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tools.bizman_foundation.fingerprint import canonical_json_bytes, canonical_sha256

_MAX_DISCOVERY_BYTES = 16 * 1024 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    lowered = host.casefold().rstrip(".")
    if lowered == "localhost":
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _validate_discovery_endpoint(endpoint: str, *, allow_remote: bool) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("DevTools endpoint must use http or https")
    if not parsed.hostname:
        raise ValueError("DevTools endpoint must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("DevTools endpoint must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(
            "DevTools endpoint must be an origin without path/query/fragment"
        )
    if not allow_remote and not _is_loopback_host(parsed.hostname):
        raise ValueError("remote DevTools endpoints are disabled by default")
    return endpoint.rstrip("/")


def _fetch_json(
    url: str,
    *,
    timeout: float,
    max_bytes: int = _MAX_DISCOVERY_BYTES,
) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "BizMan-CDP/0.1",
        },
    )
    opener = build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise OSError(
            f"DevTools discovery request failed for {url}: HTTP {exc.code}"
        ) from exc
    if len(payload) > max_bytes:
        raise ValueError(
            f"DevTools discovery document exceeds {max_bytes} bytes"
        )
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON from {url}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(
            f"DevTools discovery document at {url} must be a JSON object"
        )
    return document


@dataclass(frozen=True, slots=True)
class ProtocolCapabilities:
    commands: frozenset[str] = frozenset()
    events: frozenset[str] = frozenset()
    command_parameters: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_document(
        cls,
        document: dict[str, Any],
    ) -> "ProtocolCapabilities":
        commands: set[str] = set()
        events: set[str] = set()
        parameters: dict[str, frozenset[str]] = {}
        domains = document.get("domains", [])
        if not isinstance(domains, list):
            return cls()

        for domain in domains:
            if not isinstance(domain, dict) or not isinstance(
                domain.get("domain"), str
            ):
                continue
            domain_name = domain["domain"]

            domain_commands = domain.get("commands", [])
            if isinstance(domain_commands, list):
                for command in domain_commands:
                    if not isinstance(command, dict) or not isinstance(
                        command.get("name"), str
                    ):
                        continue
                    full_name = f"{domain_name}.{command['name']}"
                    commands.add(full_name)
                    names: set[str] = set()
                    command_params = command.get("parameters", [])
                    if isinstance(command_params, list):
                        for item in command_params:
                            if isinstance(item, dict) and isinstance(
                                item.get("name"), str
                            ):
                                names.add(item["name"])
                    parameters[full_name] = frozenset(names)

            domain_events = domain.get("events", [])
            if isinstance(domain_events, list):
                for event in domain_events:
                    if isinstance(event, dict) and isinstance(
                        event.get("name"), str
                    ):
                        events.add(f"{domain_name}.{event['name']}")

        return cls(
            commands=frozenset(commands),
            events=frozenset(events),
            command_parameters=parameters,
        )

    @property
    def is_empty(self) -> bool:
        return not self.commands and not self.events

    def has_command(self, method: str) -> bool:
        return method in self.commands

    def has_event(self, method: str) -> bool:
        return method in self.events

    def command_supports_parameter(self, method: str, parameter: str) -> bool:
        return parameter in self.command_parameters.get(method, frozenset())


@dataclass(frozen=True, slots=True)
class BrowserDiscovery:
    endpoint: str
    browser_product: str
    browser_version: str
    protocol_version: str
    user_agent: str
    websocket_url: str
    protocol_document: dict[str, Any]
    protocol_sha256: str
    capabilities: ProtocolCapabilities

    @classmethod
    def from_documents(
        cls,
        *,
        endpoint: str,
        version_document: dict[str, Any],
        protocol_document: dict[str, Any],
    ) -> "BrowserDiscovery":
        browser = version_document.get("Browser")
        websocket_url = version_document.get("webSocketDebuggerUrl")
        if not isinstance(browser, str) or not browser:
            raise ValueError("/json/version is missing Browser")
        if not isinstance(websocket_url, str) or not websocket_url:
            raise ValueError("/json/version is missing webSocketDebuggerUrl")

        websocket = urlparse(websocket_url)
        if websocket.scheme not in {"ws", "wss"} or not websocket.hostname:
            raise ValueError(
                "webSocketDebuggerUrl must be an absolute ws/wss URL"
            )

        product, separator, version = browser.partition("/")
        if not separator:
            product, version = browser, "unknown"

        declared_protocol = version_document.get("Protocol-Version")
        protocol_version = (
            declared_protocol
            if isinstance(declared_protocol, str) and declared_protocol
            else None
        )
        protocol_metadata = protocol_document.get("version")
        if protocol_version is None and isinstance(protocol_metadata, dict):
            major = protocol_metadata.get("major")
            minor = protocol_metadata.get("minor")
            if isinstance(major, str) and isinstance(minor, str):
                protocol_version = f"{major}.{minor}"
        if protocol_version is None:
            raise ValueError("unable to determine running CDP protocol version")

        user_agent = version_document.get("User-Agent")
        if not isinstance(user_agent, str):
            user_agent = ""

        return cls(
            endpoint=endpoint.rstrip("/"),
            browser_product=product,
            browser_version=version,
            protocol_version=protocol_version,
            user_agent=user_agent,
            websocket_url=websocket_url,
            protocol_document=protocol_document,
            protocol_sha256=canonical_sha256(protocol_document),
            capabilities=ProtocolCapabilities.from_document(protocol_document),
        )

    @property
    def protocol_bytes(self) -> bytes:
        return canonical_json_bytes(self.protocol_document)


def discover_browser(
    endpoint: str = "http://127.0.0.1:9222",
    *,
    timeout: float = 5.0,
    allow_remote: bool = False,
) -> BrowserDiscovery:
    normalized = _validate_discovery_endpoint(
        endpoint,
        allow_remote=allow_remote,
    )
    version_document = _fetch_json(
        f"{normalized}/json/version",
        timeout=timeout,
    )
    protocol_document = _fetch_json(
        f"{normalized}/json/protocol",
        timeout=timeout,
    )
    discovery = BrowserDiscovery.from_documents(
        endpoint=normalized,
        version_document=version_document,
        protocol_document=protocol_document,
    )
    if not allow_remote and not _is_loopback_host(
        urlparse(discovery.websocket_url).hostname
    ):
        raise ValueError(
            "remote browser WebSocket endpoints are disabled by default"
        )
    return discovery
