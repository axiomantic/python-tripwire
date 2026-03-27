"""HttpPlugin: intercepts httpx, requests, urllib, and aiohttp HTTP calls."""

import io
import json as json_module
import traceback
import urllib.request
import urllib.response
from collections.abc import Callable
from dataclasses import dataclass, field
from http.client import HTTPMessage
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

try:
    import httpx
    import requests
    import requests.adapters
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "bigfoot[http] extra is required to use HttpPlugin. Install with: pip install bigfoot[http]"
    ) from exc

try:
    import aiohttp
    import aiohttp.client_reqrep

    _AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AIOHTTP_AVAILABLE = False

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import GuardPassThrough, get_verifier_or_raise
from bigfoot._errors import ConflictError, UnmockedInteractionError
from bigfoot._firewall_request import HttpFirewallRequest
from bigfoot._normalize import normalize_url
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Import-time constants — captured BEFORE any patches are installed.
# Used by _check_conflicts() to detect foreign patchers.
# ---------------------------------------------------------------------------

_HTTPX_ORIGINAL_HANDLE: Callable[..., Any] = httpx.HTTPTransport.handle_request
_HTTPX_ORIGINAL_ASYNC_HANDLE: Callable[..., Any] = httpx.AsyncHTTPTransport.handle_async_request
_REQUESTS_ORIGINAL_SEND: Callable[..., Any] = requests.adapters.HTTPAdapter.send

_AIOHTTP_ORIGINAL_REQUEST: Callable[..., Any] | None = None
if _AIOHTTP_AVAILABLE:
    _AIOHTTP_ORIGINAL_REQUEST = aiohttp.ClientSession._request

# ---------------------------------------------------------------------------
# Module-level references to our own interceptors.
# Set during _install_patches so _check_conflicts can distinguish bigfoot
# patches from foreign patches during nested sandbox activations.
# ---------------------------------------------------------------------------

_bigfoot_httpx_handle: Callable[..., Any] | None = None
_bigfoot_httpx_async_handle: Callable[..., Any] | None = None
_bigfoot_requests_send: Callable[..., Any] | None = None
_bigfoot_aiohttp_request: Callable[..., Any] | None = None


# Sentinel: distinguishes "parameter not passed" from None in assert_request().
_ABSENT = object()

# ---------------------------------------------------------------------------
# HttpMockConfig
# ---------------------------------------------------------------------------


@dataclass
class HttpMockConfig:
    """Internal record of a registered mock response."""

    method: str
    url: str
    params: dict[str, str] | None
    response_status: int
    response_headers: dict[str, str]
    response_body: bytes
    required: bool = True
    registration_traceback: str = field(
        default_factory=lambda: "".join(traceback.format_stack()[:-2])
    )


@dataclass
class HttpErrorConfig:
    """Internal record of a registered mock error."""

    method: str
    url: str
    params: dict[str, str] | None
    raises: BaseException
    required: bool = True
    registration_traceback: str = field(
        default_factory=lambda: "".join(traceback.format_stack()[:-2])
    )


# Union type for the unified mock queue
HttpMockEntry = HttpMockConfig | HttpErrorConfig


# ---------------------------------------------------------------------------
# HttpRequestSentinel
# ---------------------------------------------------------------------------


class HttpRequestSentinel:
    """Opaque object returned by HttpPlugin.request; used as source filter in assert_interaction."""

    def __init__(self, plugin: "HttpPlugin") -> None:
        self._plugin = plugin
        self.source_id = "http:request"


# ---------------------------------------------------------------------------
# HttpAssertionBuilder
# ---------------------------------------------------------------------------


class HttpAssertionBuilder:
    """Fluent builder for asserting HTTP interactions.

    Usage::

        http.assert_request("GET", "https://example.com/api", require_response=True) \\
            .assert_response(200, {}, "")

    ``assert_request()`` with ``require_response=True`` is lazy: it records the
    expected request fields but does not touch the timeline.  ``assert_response()``
    finalises the assertion by calling ``verifier.assert_interaction()`` with all
    seven fields.
    """

    def __init__(
        self,
        verifier: "StrictVerifier",
        sentinel: HttpRequestSentinel,
        plugin: "HttpPlugin",
        method: str,
        url: str,
        headers: dict[str, Any],
        body: str,
    ) -> None:
        self._verifier = verifier
        self._sentinel = sentinel
        self._plugin = plugin
        self._method = method
        self._url = url
        self._headers = headers
        self._body = body

    def assert_response(
        self,
        status: int,
        headers: dict[str, Any],
        body: str,
    ) -> None:
        """Assert the full interaction: request fields + response fields.

        This is the terminal step that calls ``verifier.assert_interaction()``
        with all seven assertable fields.
        """
        self._plugin._asserting_request_only = False
        self._verifier.assert_interaction(
            self._sentinel,
            method=self._method,
            url=self._url,
            request_headers=self._headers,
            request_body=self._body,
            status=status,
            response_headers=headers,
            response_body=body,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_http_plugin(verifier: "StrictVerifier") -> "HttpPlugin":
    for plugin in verifier._plugins:
        if isinstance(plugin, HttpPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot HttpPlugin interceptor is active but no HttpPlugin "
        "is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Fake aiohttp response
# ---------------------------------------------------------------------------


class _FakeAiohttpResponse:
    """Lightweight stand-in for ``aiohttp.ClientResponse``.

    Only implements the subset of attributes / methods that callers typically
    use after ``await session.get(...)`` or ``await session.request(...)``.
    """

    def __init__(
        self,
        method: str,
        url: str,
        *,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        self.method = method
        self.status = status
        self.headers = headers
        self._body = body
        self.reason = "OK" if 200 <= status < 400 else "Error"
        self.content_type = headers.get("content-type", "application/octet-stream")

        if _AIOHTTP_AVAILABLE:
            from yarl import URL

            self.url = URL(url)
            self.real_url = self.url
        else:  # pragma: no cover
            object.__setattr__(self, "url", url)
            object.__setattr__(self, "real_url", url)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    async def read(self) -> bytes:
        return self._body

    async def text(self, encoding: str = "utf-8") -> str:
        return self._body.decode(encoding, errors="replace")

    async def json(
        self,
        *,
        content_type: str | None = "application/json",
        encoding: str | None = None,
    ) -> Any:  # noqa: ANN401
        return json_module.loads(self._body)

    def release(self) -> None:
        pass

    async def __aenter__(self) -> "_FakeAiohttpResponse":
        return self

    async def __aexit__(self, *args: Any) -> None:  # noqa: ANN401
        pass

    @property
    def closed(self) -> bool:
        return False

    def close(self) -> None:
        pass


def _identify_patcher(method: object) -> str:
    mod = getattr(method, "__module__", None) or ""
    qualname = getattr(method, "__qualname__", None) or ""
    if "respx" in mod or "respx" in qualname:
        return "respx"
    if "responses" in mod or "responses" in qualname:
        return "responses"
    if "httpretty" in mod:
        return "httpretty"
    return "an unknown library"


# ---------------------------------------------------------------------------
# HttpPlugin
# ---------------------------------------------------------------------------


class HttpPlugin(BasePlugin):
    """HTTP interception plugin. Requires bigfoot[http] extra.

    Patches httpx sync/async transports, requests HTTPAdapter, urllib openers,
    and aiohttp ClientSession (if installed) at the class level. Uses reference
    counting so nested sandboxes work correctly.
    """

    # Saved originals, restored when count reaches 0.
    _original_httpx_transport_handle: Callable[..., Any] | None = None
    _original_httpx_async_transport_handle: Callable[..., Any] | None = None
    _original_requests_adapter_send: Callable[..., Any] | None = None
    _original_urllib_opener: Any = None
    _original_aiohttp_request: Callable[..., Any] | None = None

    def __init__(self, verifier: "StrictVerifier", require_response: bool = False) -> None:
        super().__init__(verifier)
        self._mock_queue: list[HttpMockEntry] = []
        self._sentinel = HttpRequestSentinel(self)
        self._pass_through_rules: list[tuple[str, str]] = []
        self._asserting_request_only: bool = False
        self._require_response: bool = require_response
        self.load_config(
            self.verifier._bigfoot_config.get(self.config_key() or "", {})
        )

    @classmethod
    def config_key(cls) -> str | None:
        """Return 'http', mapping this plugin to [tool.bigfoot.http]."""
        return "http"

    def load_config(self, config: dict[str, Any]) -> None:
        """Apply [tool.bigfoot.http] configuration.

        Recognized keys:
            require_response (bool): When True, assert_request() returns an
                HttpAssertionBuilder requiring .assert_response() to complete
                the assertion. Default False.

        Unknown keys are silently ignored for forward-compatibility.
        Raises TypeError for require_response with a non-bool value.
        """
        if "require_response" in config:
            val = config["require_response"]
            if not isinstance(val, bool):
                raise TypeError(
                    f"[tool.bigfoot.http] require_response must be a bool, "
                    f"got {type(val).__name__}"
                )
            self._require_response = val

    @property
    def request(self) -> HttpRequestSentinel:
        """Sentinel used as source argument in verifier.assert_interaction()."""
        return self._sentinel

    def assert_request(
        self,
        method: str,
        url: str,
        headers: dict[str, Any] | None = None,
        body: str = "",
        raised: Any = _ABSENT,  # noqa: ANN401
        require_response: bool | None = None,
    ) -> "HttpAssertionBuilder | None":
        """Assert an HTTP request interaction, optionally requiring a chained response assertion.

        When ``raised`` is provided, the assertion is always terminal (error
        interactions have no response to chain). Returns ``None``.

        When ``require_response`` is False (the default), this method is terminal:
        it asserts only the four request fields and returns ``None``.

        When ``require_response`` is True, this method returns an
        ``HttpAssertionBuilder``.
        """
        if raised is not _ABSENT:
            # Error assertion: always request-only (no response to assert)
            self._asserting_request_only = True
            try:
                self.verifier.assert_interaction(
                    self._sentinel,
                    method=method,
                    url=url,
                    request_headers=headers if headers is not None else {},
                    request_body=body,
                    raised=raised,
                )
            finally:
                self._asserting_request_only = False
            return None

        effective = require_response if require_response is not None else self._require_response
        if not effective:
            self._asserting_request_only = True
            try:
                self.verifier.assert_interaction(
                    self._sentinel,
                    method=method,
                    url=url,
                    request_headers=headers if headers is not None else {},
                    request_body=body,
                )
            finally:
                self._asserting_request_only = False
            return None
        return HttpAssertionBuilder(
            verifier=self.verifier,
            sentinel=self._sentinel,
            plugin=self,
            method=method,
            url=url,
            headers=headers if headers is not None else {},
            body=body,
        )

    def mock_response(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        body: str | bytes | None = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock response for the given method + URL pair."""
        if json is not None and body is not None:
            raise ValueError("json and body are mutually exclusive")

        response_headers: dict[str, str] = headers or {}
        if json is not None:
            response_body = json_module.dumps(json).encode("utf-8")
            response_headers.setdefault("content-type", "application/json")
        elif body is not None:
            response_body = body.encode("utf-8") if isinstance(body, str) else body
        else:
            response_body = b""

        self._mock_queue.append(
            HttpMockConfig(
                method=method.upper(),
                url=url,
                params=params,
                response_status=status,
                response_headers=response_headers,
                response_body=response_body,
                required=required,
            )
        )

    def mock_error(
        self,
        method: str,
        url: str,
        *,
        raises: BaseException,
        params: dict[str, str] | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock error for the given method + URL pair.

        When the interceptor matches this mock, the interaction is recorded
        with request fields + raised, then the exception is re-raised into
        the code under test.

        The error config is appended to the unified mock queue alongside
        HttpMockConfig entries, preserving FIFO ordering for mixed
        success/error sequences.
        """
        self._mock_queue.append(
            HttpErrorConfig(
                method=method.upper(),
                url=url,
                params=params,
                raises=raises,
                required=required,
            )
        )

    def pass_through(self, method: str, url: str) -> None:
        """Register a permanent pass-through rule for the given method + URL.

        Requests matching this rule are forwarded to the real backend instead
        of raising UnmockedInteractionError. The interaction is still recorded
        on the timeline and must be asserted.

        The URL must match exactly (scheme, host, path). Query parameters are
        not considered for pass-through rule matching.
        """
        self._pass_through_rules.append((method.upper(), url))

    def _matches_pass_through_rule(self, method: str, url: str) -> bool:
        """Return True if method + url match any registered pass-through rule."""
        parsed_actual = urlparse(url)
        for rule_method, rule_url in self._pass_through_rules:
            if rule_method != method.upper():
                continue
            parsed_rule = urlparse(rule_url)
            if (
                parsed_rule.scheme == parsed_actual.scheme
                and parsed_rule.netloc == parsed_actual.netloc
                and parsed_rule.path == parsed_actual.path
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def check_conflicts(self) -> None:
        """Verify httpx sync/async transports and requests adapter have not been patched by a
        third party."""
        current_httpx_sync = httpx.HTTPTransport.handle_request
        if (
            current_httpx_sync is not _HTTPX_ORIGINAL_HANDLE
            and current_httpx_sync is not _bigfoot_httpx_handle
        ):
            patcher = _identify_patcher(current_httpx_sync)
            raise ConflictError(
                target="httpx.HTTPTransport.handle_request",
                patcher=patcher,
            )

        current_httpx_async = httpx.AsyncHTTPTransport.handle_async_request
        if (
            current_httpx_async is not _HTTPX_ORIGINAL_ASYNC_HANDLE
            and current_httpx_async is not _bigfoot_httpx_async_handle
        ):
            patcher = _identify_patcher(current_httpx_async)
            raise ConflictError(
                target="httpx.AsyncHTTPTransport.handle_async_request",
                patcher=patcher,
            )

        current_requests = requests.adapters.HTTPAdapter.send
        if (
            current_requests is not _REQUESTS_ORIGINAL_SEND
            and current_requests is not _bigfoot_requests_send
        ):
            patcher = _identify_patcher(current_requests)
            raise ConflictError(
                target="requests.adapters.HTTPAdapter.send",
                patcher=patcher,
            )

        if _AIOHTTP_AVAILABLE:
            current_aiohttp = aiohttp.ClientSession._request
            if (
                current_aiohttp is not _AIOHTTP_ORIGINAL_REQUEST
                and current_aiohttp is not _bigfoot_aiohttp_request
            ):
                patcher = _identify_patcher(current_aiohttp)
                raise ConflictError(
                    target="aiohttp.ClientSession._request",
                    patcher=patcher,
                )

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        global _bigfoot_httpx_handle, _bigfoot_httpx_async_handle, _bigfoot_requests_send
        global _bigfoot_aiohttp_request

        # Save originals so we can restore them later.
        HttpPlugin._original_httpx_transport_handle = httpx.HTTPTransport.handle_request
        HttpPlugin._original_httpx_async_transport_handle = (
            httpx.AsyncHTTPTransport.handle_async_request
        )
        HttpPlugin._original_requests_adapter_send = requests.adapters.HTTPAdapter.send
        _orig_httpx = HttpPlugin._original_httpx_transport_handle
        _orig_httpx_async = HttpPlugin._original_httpx_async_transport_handle
        _orig_requests_send = HttpPlugin._original_requests_adapter_send

        # httpx sync interceptor
        def _sync_interceptor(
            transport_self: httpx.HTTPTransport,
            request: httpx.Request,
        ) -> httpx.Response:
            url = str(request.url)
            method = request.method
            scheme, host, port, path = normalize_url(url)
            fw_request = HttpFirewallRequest(
                host=host, port=port, scheme=scheme, path=path, method=method,
            )
            try:
                verifier = get_verifier_or_raise("http:request", firewall_request=fw_request)
            except GuardPassThrough:
                return _orig_httpx(transport_self, request)
            plugin = _find_http_plugin(verifier)
            return plugin._handle_httpx_request(transport_self, request)

        # httpx async interceptor (NOTE: must call the async handler, not the sync one)
        async def _async_interceptor(
            transport_self: httpx.AsyncHTTPTransport,
            request: httpx.Request,
        ) -> httpx.Response:
            url = str(request.url)
            method = request.method
            scheme, host, port, path = normalize_url(url)
            fw_request = HttpFirewallRequest(
                host=host, port=port, scheme=scheme, path=path, method=method,
            )
            try:
                verifier = get_verifier_or_raise("http:request", firewall_request=fw_request)
            except GuardPassThrough:
                return await _orig_httpx_async(
                    transport_self, request,
                )
            plugin = _find_http_plugin(verifier)
            return await plugin._handle_httpx_async_request(transport_self, request)

        # requests interceptor
        def _requests_interceptor(
            adapter_self: requests.adapters.HTTPAdapter,
            request: requests.PreparedRequest,
            **kwargs: Any,  # noqa: ANN401
        ) -> requests.Response:
            url = request.url or ""
            method = (request.method or "GET").upper()
            scheme, host, port, path = normalize_url(url)
            fw_request = HttpFirewallRequest(
                host=host, port=port, scheme=scheme, path=path, method=method,
            )
            try:
                verifier = get_verifier_or_raise("http:request", firewall_request=fw_request)
            except GuardPassThrough:
                return _orig_requests_send(adapter_self, request, **kwargs)
            plugin = _find_http_plugin(verifier)
            return plugin._handle_requests_request(adapter_self, request, **kwargs)

        _bigfoot_httpx_handle = _sync_interceptor
        _bigfoot_httpx_async_handle = _async_interceptor
        _bigfoot_requests_send = _requests_interceptor

        setattr(httpx.HTTPTransport, "handle_request", _sync_interceptor)
        setattr(httpx.AsyncHTTPTransport, "handle_async_request", _async_interceptor)
        setattr(requests.adapters.HTTPAdapter, "send", _requests_interceptor)

        self._install_urllib()
        self._install_aiohttp()

    def restore_patches(self) -> None:
        global _bigfoot_httpx_handle, _bigfoot_httpx_async_handle, _bigfoot_requests_send
        global _bigfoot_aiohttp_request

        if HttpPlugin._original_httpx_transport_handle is not None:
            setattr(
                httpx.HTTPTransport, "handle_request",
                HttpPlugin._original_httpx_transport_handle,
            )
            HttpPlugin._original_httpx_transport_handle = None

        if HttpPlugin._original_httpx_async_transport_handle is not None:
            setattr(
                httpx.AsyncHTTPTransport, "handle_async_request",
                HttpPlugin._original_httpx_async_transport_handle,
            )
            HttpPlugin._original_httpx_async_transport_handle = None

        if HttpPlugin._original_requests_adapter_send is not None:
            setattr(
                requests.adapters.HTTPAdapter, "send",
                HttpPlugin._original_requests_adapter_send,
            )
            HttpPlugin._original_requests_adapter_send = None

        # urllib
        urllib.request.install_opener(HttpPlugin._original_urllib_opener)
        HttpPlugin._original_urllib_opener = None

        # aiohttp
        if _AIOHTTP_AVAILABLE and HttpPlugin._original_aiohttp_request is not None:
            setattr(aiohttp.ClientSession, "_request", HttpPlugin._original_aiohttp_request)
            HttpPlugin._original_aiohttp_request = None

        _bigfoot_httpx_handle = None
        _bigfoot_httpx_async_handle = None
        _bigfoot_requests_send = None
        _bigfoot_aiohttp_request = None

    def _install_urllib(self) -> None:
        HttpPlugin._original_urllib_opener = getattr(urllib.request, "_opener", None)

        class _BigfootHandler(urllib.request.BaseHandler):
            handler_order = 100

            def http_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return _bigfoot_urllib_dispatch(req)

            def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return _bigfoot_urllib_dispatch(req)

        def _bigfoot_urllib_dispatch(
            req: urllib.request.Request,
        ) -> urllib.response.addinfourl:
            url = req.full_url
            method = (req.get_method() or "GET").upper()
            scheme, host, port, path = normalize_url(url)
            fw_request = HttpFirewallRequest(
                host=host, port=port, scheme=scheme, path=path, method=method,
            )
            try:
                verifier = get_verifier_or_raise("http:request", firewall_request=fw_request)
            except GuardPassThrough:
                original_opener = HttpPlugin._original_urllib_opener
                urllib.request.install_opener(original_opener)
                try:
                    return cast(urllib.response.addinfourl, urllib.request.urlopen(req))
                finally:
                    HttpPlugin._reinstall_urllib_opener()
            plugin = _find_http_plugin(verifier)
            return plugin._handle_urllib_request(req)

        opener = urllib.request.build_opener(_BigfootHandler)
        urllib.request.install_opener(opener)

    def _install_aiohttp(self) -> None:
        global _bigfoot_aiohttp_request

        if not _AIOHTTP_AVAILABLE:
            return

        HttpPlugin._original_aiohttp_request = aiohttp.ClientSession._request
        _orig_aiohttp = aiohttp.ClientSession._request

        async def _aiohttp_interceptor(
            session_self: "aiohttp.ClientSession",
            method: str,
            str_or_url: Any,  # noqa: ANN401
            **kwargs: Any,  # noqa: ANN401
        ) -> Any:  # noqa: ANN401
            url = str(str_or_url)
            scheme, host, port, path = normalize_url(url)
            fw_request = HttpFirewallRequest(
                host=host, port=port, scheme=scheme, path=path, method=method,
            )
            try:
                verifier = get_verifier_or_raise("http:request", firewall_request=fw_request)
            except GuardPassThrough:
                return await _orig_aiohttp(
                    session_self, method, str_or_url, **kwargs,
                )
            plugin = _find_http_plugin(verifier)
            return await plugin._handle_aiohttp_request(session_self, method, str_or_url, **kwargs)

        _bigfoot_aiohttp_request = _aiohttp_interceptor
        setattr(aiohttp.ClientSession, "_request", _aiohttp_interceptor)

    # ------------------------------------------------------------------
    # Mock config lookup
    # ------------------------------------------------------------------

    def _find_matching_config(self, method: str, url: str) -> HttpMockEntry | None:
        for i, config in enumerate(self._mock_queue):
            if config.method == method.upper() and self._url_matches(config, url):
                self._mock_queue.pop(i)
                return config
        return None

    def _url_matches(self, config: HttpMockEntry, actual_url: str) -> bool:
        config_parsed = urlparse(config.url)
        actual_parsed = urlparse(actual_url)

        if (
            config_parsed.scheme != actual_parsed.scheme
            or config_parsed.netloc != actual_parsed.netloc
            or config_parsed.path != actual_parsed.path
        ):
            return False

        if config.params is not None:
            actual_params = parse_qs(actual_parsed.query)
            for key, val in config.params.items():
                if key not in actual_params or val not in actual_params[key]:
                    return False

        return True

    # ------------------------------------------------------------------
    # Interaction recording
    # ------------------------------------------------------------------

    def _record_http_interaction(
        self,
        method: str,
        url: str,
        request_headers: dict[str, str],
        request_body: str,
        status: int,
        response_headers: dict[str, str],
        response_body: str,
    ) -> None:
        interaction = Interaction(
            source_id="http:request",
            sequence=0,
            details={
                "method": method.upper(),
                "url": url,
                "request_headers": dict(request_headers),
                "request_body": request_body,
                "status": status,
                "response_headers": dict(response_headers),
                "response_body": response_body,
            },
            plugin=self,
        )
        self.record(interaction)

    def _record_http_error_interaction(
        self,
        method: str,
        url: str,
        request_headers: dict[str, str],
        request_body: str,
        raised: BaseException,
    ) -> None:
        """Record an error interaction: request fields + raised, no response fields."""
        interaction = Interaction(
            source_id="http:request",
            sequence=0,
            details={
                "method": method.upper(),
                "url": url,
                "request_headers": dict(request_headers),
                "request_body": request_body,
                "raised": raised,
            },
            plugin=self,
        )
        self.record(interaction)

    # ------------------------------------------------------------------
    # Request handlers — one per backend
    # ------------------------------------------------------------------

    def _handle_httpx_request(
        self, transport_self: httpx.HTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        method = request.method
        url = str(request.url)

        if self._matches_pass_through_rule(method, url):
            return self._execute_httpx_pass_through(transport_self, request)

        config = self._find_matching_config(method, url)

        if config is None:
            hint = self.format_unmocked_hint("http:request", (method, url), {})
            raise UnmockedInteractionError(
                source_id="http:request",
                args=(method, url),
                kwargs={},
                hint=hint,
            )

        if isinstance(config, HttpErrorConfig):
            body_str = request.content.decode("utf-8", errors="replace")
            self._record_http_error_interaction(
                method=method,
                url=url,
                request_headers=dict(request.headers),
                request_body=body_str,
                raised=config.raises,
            )
            raise config.raises

        body_str = request.content.decode("utf-8", errors="replace")
        resp_body_str = config.response_body.decode("utf-8", errors="replace")
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=dict(request.headers),
            request_body=body_str,
            status=config.response_status,
            response_headers=dict(config.response_headers),
            response_body=resp_body_str,
        )

        return httpx.Response(
            status_code=config.response_status,
            headers=config.response_headers,
            content=config.response_body,
        )

    def _execute_httpx_pass_through(
        self, transport_self: httpx.HTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        """Forward an httpx request to the real backend and record the interaction."""
        original = HttpPlugin._original_httpx_transport_handle
        assert original is not None
        response: httpx.Response = original(transport_self, request)
        self._record_http_interaction(
            method=request.method,
            url=str(request.url),
            request_headers=dict(request.headers),
            request_body=request.content.decode("utf-8", errors="replace"),
            status=response.status_code,
            response_headers=dict(response.headers),
            response_body=response.text,
        )
        return response

    async def _handle_httpx_async_request(
        self, transport_self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        """Async variant of _handle_httpx_request."""
        method = request.method
        url = str(request.url)

        if self._matches_pass_through_rule(method, url):
            return await self._execute_httpx_async_pass_through(transport_self, request)

        config = self._find_matching_config(method, url)

        if config is None:
            hint = self.format_unmocked_hint("http:request", (method, url), {})
            raise UnmockedInteractionError(
                source_id="http:request",
                args=(method, url),
                kwargs={},
                hint=hint,
            )

        if isinstance(config, HttpErrorConfig):
            body_str = request.content.decode("utf-8", errors="replace")
            self._record_http_error_interaction(
                method=method,
                url=url,
                request_headers=dict(request.headers),
                request_body=body_str,
                raised=config.raises,
            )
            raise config.raises

        body_str = request.content.decode("utf-8", errors="replace")
        resp_body_str = config.response_body.decode("utf-8", errors="replace")
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=dict(request.headers),
            request_body=body_str,
            status=config.response_status,
            response_headers=dict(config.response_headers),
            response_body=resp_body_str,
        )

        return httpx.Response(
            status_code=config.response_status,
            headers=config.response_headers,
            content=config.response_body,
        )

    async def _execute_httpx_async_pass_through(
        self, transport_self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        """Forward an async httpx request to the real backend and record the interaction."""
        original = HttpPlugin._original_httpx_async_transport_handle
        assert original is not None
        response: httpx.Response = await original(transport_self, request)
        self._record_http_interaction(
            method=request.method,
            url=str(request.url),
            request_headers=dict(request.headers),
            request_body=request.content.decode("utf-8", errors="replace"),
            status=response.status_code,
            response_headers=dict(response.headers),
            response_body=response.text,
        )
        return response

    def _handle_requests_request(
        self,
        adapter_self: requests.adapters.HTTPAdapter,
        request: requests.PreparedRequest,
        **kwargs: Any,  # noqa: ANN401
    ) -> requests.Response:
        method = (request.method or "GET").upper()
        url = request.url or ""

        if self._matches_pass_through_rule(method, url):
            return self._execute_requests_pass_through(adapter_self, request, **kwargs)

        config = self._find_matching_config(method, url)

        if config is None:
            hint = self.format_unmocked_hint("http:request", (method, url), {})
            raise UnmockedInteractionError(
                source_id="http:request",
                args=(method, url),
                kwargs={},
                hint=hint,
            )

        if isinstance(config, HttpErrorConfig):
            body_str = ""
            if request.body:
                if isinstance(request.body, bytes):
                    body_str = request.body.decode("utf-8", errors="replace")
                else:
                    body_str = str(request.body)
            self._record_http_error_interaction(
                method=method,
                url=url,
                request_headers=dict(request.headers),
                request_body=body_str,
                raised=config.raises,
            )
            raise config.raises

        body_str = ""
        if request.body:
            if isinstance(request.body, bytes):
                body_str = request.body.decode("utf-8", errors="replace")
            else:
                body_str = str(request.body)

        resp_body_str = config.response_body.decode("utf-8", errors="replace")
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=dict(request.headers),
            request_body=body_str,
            status=config.response_status,
            response_headers=dict(config.response_headers),
            response_body=resp_body_str,
        )

        response = requests.Response()
        response.status_code = config.response_status
        response.headers.update(config.response_headers)
        response._content = config.response_body
        response.encoding = "utf-8"
        response.url = url
        response.request = request
        return response

    def _execute_requests_pass_through(
        self,
        adapter_self: requests.adapters.HTTPAdapter,
        request: requests.PreparedRequest,
        **kwargs: Any,  # noqa: ANN401
    ) -> requests.Response:
        """Forward a requests request to the real backend and record the interaction."""
        original = HttpPlugin._original_requests_adapter_send
        assert original is not None
        response: requests.Response = original(adapter_self, request, **kwargs)
        method = (request.method or "GET").upper()
        url = request.url or ""
        body_str = ""
        if request.body:
            if isinstance(request.body, bytes):
                body_str = request.body.decode("utf-8", errors="replace")
            else:
                body_str = str(request.body)
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=dict(request.headers),
            request_body=body_str,
            status=response.status_code,
            response_headers=dict(response.headers),
            response_body=response.text,
        )
        return response

    def _handle_urllib_request(self, req: urllib.request.Request) -> urllib.response.addinfourl:
        method = (req.get_method() or "GET").upper()
        url = req.full_url

        if self._matches_pass_through_rule(method, url):
            return self._execute_urllib_pass_through(req)

        config = self._find_matching_config(method, url)

        if config is None:
            hint = self.format_unmocked_hint("http:request", (method, url), {})
            raise UnmockedInteractionError(
                source_id="http:request",
                args=(method, url),
                kwargs={},
                hint=hint,
            )

        if isinstance(config, HttpErrorConfig):
            headers_dict = dict(req.headers)
            data = req.data
            body_str = ""
            if data:
                body_str = (
                    data.decode("utf-8", errors="replace")
                    if isinstance(data, bytes)
                    else str(data)
                )
            self._record_http_error_interaction(
                method=method,
                url=url,
                request_headers=headers_dict,
                request_body=body_str,
                raised=config.raises,
            )
            raise config.raises

        headers_dict = dict(req.headers)
        data = req.data
        body_str = ""
        if data:
            body_str = (
                data.decode("utf-8", errors="replace")  # pragma: no cover
                if isinstance(data, bytes)
                else str(data)  # pragma: no cover
            )

        resp_body_str = config.response_body.decode("utf-8", errors="replace")
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=headers_dict,
            request_body=body_str,
            status=config.response_status,
            response_headers=dict(config.response_headers),
            response_body=resp_body_str,
        )

        msg = HTTPMessage()
        for k, v in config.response_headers.items():
            msg[k] = v

        response = urllib.response.addinfourl(
            io.BytesIO(config.response_body),
            msg,
            url,
            config.response_status,
        )
        # urllib's HTTPErrorProcessor accesses response.msg (the HTTP reason phrase).
        # addinfourl delegates unknown attributes to its fp (BytesIO), which has no 'msg'.
        # We set it directly so the standard urllib response-processing chain works.
        setattr(response, "msg", "OK")  # addinfourl has no typed msg attr; urllib needs it
        return response

    def _execute_urllib_pass_through(
        self, req: urllib.request.Request
    ) -> urllib.response.addinfourl:
        """Forward a urllib request to the real backend and record the interaction."""
        original_opener = HttpPlugin._original_urllib_opener
        # Restore original opener temporarily, make the real request, then reinstall bigfoot's
        urllib.request.install_opener(original_opener)
        try:
            response: urllib.response.addinfourl = urllib.request.urlopen(req)
        finally:
            # Reinstall bigfoot's opener regardless of outcome
            from bigfoot.plugins.http import HttpPlugin as _Self  # noqa: PLC0415

            _Self._reinstall_urllib_opener()
        method = (req.get_method() or "GET").upper()
        url = req.full_url
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=dict(req.headers),
            request_body="",
            status=response.getcode() or 200,
            response_headers={},
            response_body="",
        )
        return response

    async def _handle_aiohttp_request(
        self,
        session_self: Any,  # noqa: ANN401
        method: str,
        str_or_url: Any,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> "_FakeAiohttpResponse":
        """Handle an intercepted aiohttp request."""
        url = str(str_or_url)

        if self._matches_pass_through_rule(method, url):
            result: _FakeAiohttpResponse = await self._execute_aiohttp_pass_through(
                session_self, method, str_or_url, **kwargs
            )
            return result

        config = self._find_matching_config(method, url)

        if config is None:
            hint = self.format_unmocked_hint("http:request", (method, url), {})
            raise UnmockedInteractionError(
                source_id="http:request",
                args=(method, url),
                kwargs={},
                hint=hint,
            )

        if isinstance(config, HttpErrorConfig):
            body_str = ""
            if "data" in kwargs and kwargs["data"] is not None:
                data = kwargs["data"]
                if isinstance(data, bytes):
                    body_str = data.decode("utf-8", errors="replace")
                elif isinstance(data, str):
                    body_str = data
                else:
                    body_str = str(data)
            elif "json" in kwargs and kwargs["json"] is not None:
                body_str = json_module.dumps(kwargs["json"])
            req_headers: dict[str, str] = {}
            if "headers" in kwargs and kwargs["headers"] is not None:
                req_headers = dict(kwargs["headers"])
            self._record_http_error_interaction(
                method=method,
                url=url,
                request_headers=req_headers,
                request_body=body_str,
                raised=config.raises,
            )
            raise config.raises

        # Extract request body from kwargs
        body_str = ""
        if "data" in kwargs and kwargs["data"] is not None:
            data = kwargs["data"]
            if isinstance(data, bytes):
                body_str = data.decode("utf-8", errors="replace")
            elif isinstance(data, str):
                body_str = data
            else:
                body_str = str(data)
        elif "json" in kwargs and kwargs["json"] is not None:
            body_str = json_module.dumps(kwargs["json"])

        # Extract request headers from kwargs
        req_headers_success: dict[str, str] = {}
        if "headers" in kwargs and kwargs["headers"] is not None:
            req_headers_success = dict(kwargs["headers"])

        resp_body_str = config.response_body.decode("utf-8", errors="replace")
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=req_headers_success,
            request_body=body_str,
            status=config.response_status,
            response_headers=dict(config.response_headers),
            response_body=resp_body_str,
        )

        return _FakeAiohttpResponse(
            method=method,
            url=url,
            status=config.response_status,
            headers=config.response_headers,
            body=config.response_body,
        )

    async def _execute_aiohttp_pass_through(
        self,
        session_self: Any,  # noqa: ANN401
        method: str,
        str_or_url: Any,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Forward an aiohttp request to the real backend and record the interaction."""
        original = HttpPlugin._original_aiohttp_request
        assert original is not None
        response = await original(session_self, method, str_or_url, **kwargs)
        url = str(str_or_url)
        body_str = ""
        if "data" in kwargs and kwargs["data"] is not None:
            data = kwargs["data"]
            if isinstance(data, bytes):
                body_str = data.decode("utf-8", errors="replace")
            elif isinstance(data, str):
                body_str = data
            else:
                body_str = str(data)
        elif "json" in kwargs and kwargs["json"] is not None:
            body_str = json_module.dumps(kwargs["json"])
        req_headers: dict[str, str] = {}
        if "headers" in kwargs and kwargs["headers"] is not None:
            req_headers = dict(kwargs["headers"])
        resp_body = await response.read()
        self._record_http_interaction(
            method=method,
            url=url,
            request_headers=req_headers,
            request_body=body_str,
            status=response.status,
            response_headers=dict(response.headers),
            response_body=resp_body.decode("utf-8", errors="replace"),
        )
        return response

    @classmethod
    def _reinstall_urllib_opener(cls) -> None:
        """Reinstall bigfoot's urllib opener after a pass-through call."""

        # Build a fresh handler using the same dispatch function used in _install_urllib
        # We call _install_urllib again but only the opener part.
        # This is safe because _original_urllib_opener is still set at the class level.
        class _BigfootHandler(urllib.request.BaseHandler):
            handler_order = 100

            def http_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return _bigfoot_urllib_dispatch_ref(req)

            def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return _bigfoot_urllib_dispatch_ref(req)

        def _bigfoot_urllib_dispatch_ref(
            req: urllib.request.Request,
        ) -> urllib.response.addinfourl:
            url = req.full_url
            method = (req.get_method() or "GET").upper()
            scheme, host, port, path = normalize_url(url)
            fw_request = HttpFirewallRequest(
                host=host, port=port, scheme=scheme, path=path, method=method,
            )
            try:
                verifier = get_verifier_or_raise("http:request", firewall_request=fw_request)
            except GuardPassThrough:
                original_opener = HttpPlugin._original_urllib_opener
                urllib.request.install_opener(original_opener)
                try:
                    return cast(urllib.response.addinfourl, urllib.request.urlopen(req))
                finally:
                    HttpPlugin._reinstall_urllib_opener()
            plugin = _find_http_plugin(verifier)
            return plugin._handle_urllib_request(req)

        opener = urllib.request.build_opener(_BigfootHandler)
        urllib.request.install_opener(opener)

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        try:
            for key, expected_val in expected.items():
                actual_val = interaction.details.get(key)
                if not (expected_val == actual_val):
                    return False
            return True
        except Exception:
            return False

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        url = interaction.details.get("url", "?")
        if "raised" in interaction.details:
            raised = interaction.details["raised"]
            exc_type = type(raised).__module__ + "." + type(raised).__qualname__
            return f"[HttpPlugin] {method} {url} -> raised {exc_type}({raised!s})"
        status = interaction.details.get("status", "?")
        return f"[HttpPlugin] {method} {url} (status={status})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "GET")
        url = interaction.details.get("url", "https://example.com/path")
        if "raised" in interaction.details:
            raised = interaction.details["raised"]
            return f'http.mock_error("{method}", "{url}", raises={raised!r})'
        return f'http.mock_response("{method}", "{url}", json={{...}})'

    def format_unmocked_hint(
        self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        method = args[0] if args else "GET"
        url = args[1] if len(args) > 1 else "https://example.com/path"
        return (
            f"Unexpected HTTP request: {method} {url}\n\n"
            f"  To mock this request, add before your sandbox:\n"
            f'    http.mock_response("{method}", "{url}", json={{...}})\n\n'
            f"  Or to mock an error:\n"
            f'    http.mock_error("{method}", "{url}", raises=ConnectionError(...))\n\n'
            f"  Or to mark it optional:\n"
            f'    http.mock_response("{method}", "{url}", json={{...}}, required=False)'
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "GET")
        url = interaction.details.get("url", "?")
        request_headers = interaction.details.get("request_headers", {})
        request_body = interaction.details.get("request_body", "")

        if "raised" in interaction.details:
            raised = interaction.details["raised"]
            return (
                f"http.assert_request(\n"
                f'    "{method}",\n'
                f'    "{url}",\n'
                f"    headers={request_headers!r},\n"
                f"    body={request_body!r},\n"
                f"    raised={raised!r},\n"
                f")"
            )

        if self._require_response:
            status = interaction.details.get("status", 200)
            response_headers = interaction.details.get("response_headers", {})
            response_body = interaction.details.get("response_body", "")
            return (
                f"http.assert_request(\n"
                f'    "{method}",\n'
                f'    "{url}",\n'
                f"    headers={request_headers!r},\n"
                f"    body={request_body!r},\n"
                f"    require_response=True,\n"
                f").assert_response(\n"
                f"    status={status},\n"
                f"    headers={response_headers!r},\n"
                f"    body={response_body!r},\n"
                f")"
            )
        return (
            f"http.assert_request(\n"
            f'    "{method}",\n'
            f'    "{url}",\n'
            f"    headers={request_headers!r},\n"
            f"    body={request_body!r},\n"
            f")"
        )

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """Return the field names required in **expected when asserting an HTTP interaction.

        Error interactions (raised in details): request fields + raised.
        Request-only mode: four request fields.
        Full mode: all seven fields.
        """
        if "raised" in interaction.details:
            return frozenset({"method", "url", "request_headers", "request_body", "raised"})
        if self._asserting_request_only:
            return frozenset({"method", "url", "request_headers", "request_body"})
        return frozenset(
            {
                "method", "url", "request_headers", "request_body",
                "status", "response_headers", "response_body",
            }
        )

    def get_unused_mocks(self) -> list[HttpMockEntry]:
        return [c for c in self._mock_queue if c.required]

    def format_unused_mock_hint(self, mock_config: object) -> str:
        if isinstance(mock_config, HttpErrorConfig):
            config = mock_config
            raised = config.raises
            return (
                f"http:{config.method} {config.url} error mock was registered but never called.\n"
                f"    Configured to raise: {raised!r}\n"
                f"    Mock registered at:\n"
                f"{config.registration_traceback}\n"
                f"    Options:\n"
                f"      - Remove this mock if it's not needed\n"
                f'      - Mark it optional: http.mock_error("{config.method}", '
                f'"{config.url}", raises=..., required=False)'
            )
        assert isinstance(mock_config, HttpMockConfig)
        return (
            f"http:{mock_config.method} {mock_config.url} was registered but never called.\n"
            f"    Mock registered at:\n"
            f"{mock_config.registration_traceback}\n"
            f"    Options:\n"
            f"      - Remove this mock if it's not needed\n"
            f'      - Mark it optional: http.mock_response("{mock_config.method}", '
            f'"{mock_config.url}", ..., required=False)'
        )
