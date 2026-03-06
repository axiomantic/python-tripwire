"""HttpPlugin: intercepts httpx, requests, and urllib HTTP calls."""

import functools
import io
import json as json_module
import threading
import traceback
import urllib.request
import urllib.response
from dataclasses import dataclass, field
from http.client import HTTPMessage
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

try:
    import httpx
    import requests
    import requests.adapters
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "bigfoot[http] extra is required to use HttpPlugin. Install with: pip install bigfoot[http]"
    ) from exc

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import ConflictError, UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Import-time constants — captured BEFORE any patches are installed.
# Used by _check_conflicts() to detect foreign patchers.
# ---------------------------------------------------------------------------

_HTTPX_ORIGINAL_HANDLE: Any = httpx.HTTPTransport.handle_request
_HTTPX_ORIGINAL_ASYNC_HANDLE: Any = httpx.AsyncHTTPTransport.handle_async_request
_REQUESTS_ORIGINAL_SEND: Any = requests.adapters.HTTPAdapter.send

# ---------------------------------------------------------------------------
# Module-level references to our own interceptors.
# Set during _install_patches so _check_conflicts can distinguish bigfoot
# patches from foreign patches during nested sandbox activations.
# ---------------------------------------------------------------------------

_bigfoot_httpx_handle: Any = None
_bigfoot_httpx_async_handle: Any = None
_bigfoot_requests_send: Any = None


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

        http.assert_request("GET", "https://example.com/api") \\
            .assert_response(200, {}, "")

    ``assert_request()`` is lazy: it records the expected request fields but does
    not touch the timeline.  ``assert_response()`` finalises the assertion by
    calling ``verifier.assert_interaction()`` with all seven fields.
    """

    def __init__(
        self,
        verifier: "StrictVerifier",
        sentinel: HttpRequestSentinel,
        method: str,
        url: str,
        headers: dict[str, Any],
        body: str,
    ) -> None:
        self._verifier = verifier
        self._sentinel = sentinel
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
    and asyncio.BaseEventLoop.run_in_executor at the class level. Uses
    reference counting so nested sandboxes work correctly.
    """

    # Class-level reference counting — shared across all instances/verifiers.
    _install_count: int = 0
    _install_lock: threading.Lock = threading.Lock()

    # Saved originals, restored when count reaches 0.
    _original_httpx_transport_handle: Any = None
    _original_httpx_async_transport_handle: Any = None
    _original_requests_adapter_send: Any = None
    _original_urllib_opener: Any = None
    _original_run_in_executor: Any = None

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._mock_queue: list[HttpMockConfig] = []
        self._sentinel = HttpRequestSentinel(self)
        self._pass_through_rules: list[tuple[str, str]] = []

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
    ) -> "HttpAssertionBuilder":
        """Return an HttpAssertionBuilder pre-loaded with expected request fields.

        Call ``.assert_response()`` on the returned builder to complete the
        assertion with all seven fields.
        """
        return HttpAssertionBuilder(
            verifier=self.verifier,
            sentinel=self._sentinel,
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

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        with HttpPlugin._install_lock:
            if HttpPlugin._install_count == 0:
                self._check_conflicts()
                self._install_patches()
            HttpPlugin._install_count += 1

    def deactivate(self) -> None:
        with HttpPlugin._install_lock:
            HttpPlugin._install_count = max(0, HttpPlugin._install_count - 1)
            if HttpPlugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _check_conflicts(self) -> None:
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

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        global _bigfoot_httpx_handle, _bigfoot_httpx_async_handle, _bigfoot_requests_send

        # Save originals so we can restore them later.
        HttpPlugin._original_httpx_transport_handle = httpx.HTTPTransport.handle_request
        HttpPlugin._original_httpx_async_transport_handle = (
            httpx.AsyncHTTPTransport.handle_async_request
        )
        HttpPlugin._original_requests_adapter_send = requests.adapters.HTTPAdapter.send

        # httpx sync interceptor
        def _sync_interceptor(
            transport_self: httpx.HTTPTransport,
            request: httpx.Request,
        ) -> httpx.Response:
            verifier = _get_verifier_or_raise("http:request")
            plugin = _find_http_plugin(verifier)
            return plugin._handle_httpx_request(transport_self, request)

        # httpx async interceptor (NOTE: must call the async handler, not the sync one)
        async def _async_interceptor(
            transport_self: httpx.AsyncHTTPTransport,
            request: httpx.Request,
        ) -> httpx.Response:
            verifier = _get_verifier_or_raise("http:request")
            plugin = _find_http_plugin(verifier)
            return await plugin._handle_httpx_async_request(transport_self, request)

        # requests interceptor
        def _requests_interceptor(
            adapter_self: requests.adapters.HTTPAdapter,
            request: requests.PreparedRequest,
            **kwargs: Any,  # noqa: ANN401
        ) -> requests.Response:
            verifier = _get_verifier_or_raise("http:request")
            plugin = _find_http_plugin(verifier)
            return plugin._handle_requests_request(adapter_self, request, **kwargs)

        _bigfoot_httpx_handle = _sync_interceptor
        _bigfoot_httpx_async_handle = _async_interceptor
        _bigfoot_requests_send = _requests_interceptor

        httpx.HTTPTransport.handle_request = _sync_interceptor  # type: ignore[assignment]
        httpx.AsyncHTTPTransport.handle_async_request = _async_interceptor  # type: ignore[assignment]
        requests.adapters.HTTPAdapter.send = _requests_interceptor  # type: ignore[assignment]

        self._install_urllib()
        self._patch_run_in_executor()

    def _restore_patches(self) -> None:
        global _bigfoot_httpx_handle, _bigfoot_httpx_async_handle, _bigfoot_requests_send

        if HttpPlugin._original_httpx_transport_handle is not None:
            httpx.HTTPTransport.handle_request = HttpPlugin._original_httpx_transport_handle  # type: ignore[method-assign]
            HttpPlugin._original_httpx_transport_handle = None

        if HttpPlugin._original_httpx_async_transport_handle is not None:
            httpx.AsyncHTTPTransport.handle_async_request = (  # type: ignore[method-assign]
                HttpPlugin._original_httpx_async_transport_handle
            )
            HttpPlugin._original_httpx_async_transport_handle = None

        if HttpPlugin._original_requests_adapter_send is not None:
            requests.adapters.HTTPAdapter.send = HttpPlugin._original_requests_adapter_send  # type: ignore[method-assign]
            HttpPlugin._original_requests_adapter_send = None

        # urllib
        urllib.request.install_opener(HttpPlugin._original_urllib_opener)
        HttpPlugin._original_urllib_opener = None

        # run_in_executor
        import asyncio

        if HttpPlugin._original_run_in_executor is not None:
            asyncio.BaseEventLoop.run_in_executor = HttpPlugin._original_run_in_executor  # type: ignore[method-assign]
            HttpPlugin._original_run_in_executor = None

        _bigfoot_httpx_handle = None
        _bigfoot_httpx_async_handle = None
        _bigfoot_requests_send = None

    def _install_urllib(self) -> None:
        HttpPlugin._original_urllib_opener = urllib.request._opener  # type: ignore[attr-defined]

        class _BigfootHandler(urllib.request.BaseHandler):
            handler_order = 100

            def http_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return _bigfoot_urllib_dispatch(req)

            def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return _bigfoot_urllib_dispatch(req)

        def _bigfoot_urllib_dispatch(
            req: urllib.request.Request,
        ) -> urllib.response.addinfourl:
            verifier = _get_verifier_or_raise("http:request")
            plugin = _find_http_plugin(verifier)
            return plugin._handle_urllib_request(req)

        opener = urllib.request.build_opener(_BigfootHandler)
        urllib.request.install_opener(opener)

    def _patch_run_in_executor(self) -> None:
        import asyncio
        import contextvars

        _original = asyncio.BaseEventLoop.run_in_executor
        HttpPlugin._original_run_in_executor = _original

        def _patched_run_in_executor(
            loop: asyncio.BaseEventLoop,
            executor: Any,  # noqa: ANN401
            func: Any,  # noqa: ANN401
            *args: Any,  # noqa: ANN401
        ) -> Any:  # noqa: ANN401
            ctx = contextvars.copy_context()
            wrapped = functools.partial(ctx.run, func, *args)
            return _original(loop, executor, wrapped)

        asyncio.BaseEventLoop.run_in_executor = _patched_run_in_executor  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Mock config lookup
    # ------------------------------------------------------------------

    def _find_matching_config(self, method: str, url: str) -> HttpMockConfig | None:
        for i, config in enumerate(self._mock_queue):
            if config.method == method.upper() and self._url_matches(config, url):
                self._mock_queue.pop(i)
                return config
        return None

    def _url_matches(self, config: HttpMockConfig, actual_url: str) -> bool:
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
        self.verifier._timeline.append(interaction)

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
            verifier = _get_verifier_or_raise("http:request")
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
        status = interaction.details.get("status", "?")
        return f"[HttpPlugin] {method} {url} (status={status})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "GET")
        url = interaction.details.get("url", "https://example.com/path")
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
            f"  Or to mark it optional:\n"
            f'    http.mock_response("{method}", "{url}", json={{...}}, required=False)'
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "GET")
        url = interaction.details.get("url", "?")
        request_headers = interaction.details.get("request_headers", {})
        request_body = interaction.details.get("request_body", "")
        status = interaction.details.get("status", 200)
        response_headers = interaction.details.get("response_headers", {})
        response_body = interaction.details.get("response_body", "")
        return (
            f"verifier.assert_interaction(\n"
            f"    http.request,\n"
            f'    method="{method}",\n'
            f'    url="{url}",\n'
            f"    request_headers={request_headers!r},\n"
            f"    request_body={request_body!r},\n"
            f"    status={status},\n"
            f"    response_headers={response_headers!r},\n"
            f"    response_body={response_body!r},\n"
            f")"
        )

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """Return the field names required in **expected when asserting an HTTP interaction."""
        return frozenset(
            {
                "method", "url", "request_headers", "request_body",
                "status", "response_headers", "response_body",
            }
        )

    def get_unused_mocks(self) -> list[HttpMockConfig]:
        return [c for c in self._mock_queue if c.required]

    def format_unused_mock_hint(self, mock_config: object) -> str:
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
