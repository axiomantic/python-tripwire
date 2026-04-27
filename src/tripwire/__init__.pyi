"""Type stubs for tripwire's dynamic module-level API.

Enables Pyright/mypy to resolve:
- ``with tripwire:`` context manager protocol
- Module-level functions (current_verifier, sandbox, assert_interaction, etc.)
- Module-level factories (mock, spy)
- Plugin proxy attributes (http, subprocess_mock, etc.)
- All error classes
"""

from __future__ import annotations

import types
from typing import Any

from tripwire._base_plugin import BasePlugin as BasePlugin
from tripwire._context import GuardPassThrough as GuardPassThrough
from tripwire._context import get_verifier_or_raise as get_verifier_or_raise
from tripwire._errors import AllWildcardAssertionError as AllWildcardAssertionError
from tripwire._errors import AssertionInsideSandboxError as AssertionInsideSandboxError
from tripwire._errors import AutoAssertError as AutoAssertError
from tripwire._errors import ConflictError as ConflictError
from tripwire._errors import GuardedCallError as GuardedCallError
from tripwire._errors import GuardedCallWarning as GuardedCallWarning
from tripwire._errors import InteractionMismatchError as InteractionMismatchError
from tripwire._errors import InvalidStateError as InvalidStateError
from tripwire._errors import MissingAssertionFieldsError as MissingAssertionFieldsError
from tripwire._errors import NoActiveVerifierError as NoActiveVerifierError
from tripwire._errors import SandboxNotActiveError as SandboxNotActiveError
from tripwire._errors import TripwireConfigError as TripwireConfigError
from tripwire._errors import TripwireError as TripwireError
from tripwire._errors import UnassertedInteractionsError as UnassertedInteractionsError
from tripwire._errors import UnmockedInteractionError as UnmockedInteractionError
from tripwire._errors import UnusedMocksError as UnusedMocksError
from tripwire._errors import VerificationError as VerificationError
from tripwire._guard import allow as allow
from tripwire._guard import deny as deny
from tripwire._mock_plugin import ImportSiteMock, ObjectMock
from tripwire._mock_plugin import MockPlugin as MockPlugin
from tripwire._registry import PluginEntry as PluginEntry
from tripwire._registry import is_guard_eligible as is_guard_eligible
from tripwire._timeline import Interaction as Interaction
from tripwire._timeline import Timeline as Timeline
from tripwire._verifier import InAnyOrderContext as InAnyOrderContext
from tripwire._verifier import SandboxContext as SandboxContext
from tripwire._verifier import StrictVerifier as StrictVerifier
from tripwire.plugins.async_subprocess_plugin import (
    AsyncSubprocessPlugin as AsyncSubprocessPlugin,
)
from tripwire.plugins.database_plugin import DatabasePlugin as DatabasePlugin
from tripwire.plugins.dns_plugin import DnsPlugin as DnsPlugin
from tripwire.plugins.file_io_plugin import FileIoPlugin as FileIoPlugin
from tripwire.plugins.logging_plugin import LoggingPlugin as LoggingPlugin
from tripwire.plugins.memcache_plugin import MemcachePlugin as MemcachePlugin
from tripwire.plugins.native_plugin import NativePlugin as NativePlugin
from tripwire.plugins.popen_plugin import PopenPlugin as PopenPlugin
from tripwire.plugins.redis_plugin import RedisPlugin as RedisPlugin
from tripwire.plugins.smtp_plugin import SmtpPlugin as SmtpPlugin
from tripwire.plugins.socket_plugin import SocketPlugin as SocketPlugin
from tripwire.plugins.subprocess import SubprocessPlugin as SubprocessPlugin
from tripwire.plugins.websocket_plugin import (
    AsyncWebSocketPlugin as AsyncWebSocketPlugin,
)
from tripwire.plugins.websocket_plugin import (
    SyncWebSocketPlugin as SyncWebSocketPlugin,
)

# Optional plugin classes (may not be importable if extras not installed)
try:
    from tripwire.plugins.http import HttpPlugin as HttpPlugin
except ImportError: ...

try:
    from tripwire.plugins.celery_plugin import CeleryPlugin as CeleryPlugin
except ImportError: ...

try:
    from tripwire.plugins.boto3_plugin import Boto3Plugin as Boto3Plugin
except ImportError: ...

try:
    from tripwire.plugins.elasticsearch_plugin import (
        ElasticsearchPlugin as ElasticsearchPlugin,
    )
except ImportError: ...

try:
    from tripwire.plugins.jwt_plugin import JwtPlugin as JwtPlugin
except ImportError: ...

try:
    from tripwire.plugins.crypto_plugin import CryptoPlugin as CryptoPlugin
except ImportError: ...

try:
    from tripwire.plugins.mongo_plugin import MongoPlugin as MongoPlugin
except ImportError: ...

try:
    from tripwire.plugins.pika_plugin import PikaPlugin as PikaPlugin
except ImportError: ...

try:
    from tripwire.plugins.ssh_plugin import SshPlugin as SshPlugin
except ImportError: ...

try:
    from tripwire.plugins.grpc_plugin import GrpcPlugin as GrpcPlugin
except ImportError: ...

try:
    from tripwire.plugins.mcp_plugin import McpPlugin as McpPlugin
except ImportError: ...

try:
    from tripwire.plugins.psycopg2_plugin import Psycopg2Plugin as Psycopg2Plugin
except ImportError: ...

try:
    from tripwire.plugins.asyncpg_plugin import AsyncpgPlugin as AsyncpgPlugin
except ImportError: ...

# ---------------------------------------------------------------------------
# Module-level context manager protocol
# ---------------------------------------------------------------------------

def __enter__() -> StrictVerifier: ...  # noqa: N807
def __exit__(  # noqa: N807
    __exc_type: type[BaseException] | None,
    __exc_val: BaseException | None,
    __exc_tb: types.TracebackType | None,
) -> None: ...
async def __aenter__() -> StrictVerifier: ...  # noqa: N807
async def __aexit__(  # noqa: N807
    __exc_type: type[BaseException] | None,
    __exc_val: BaseException | None,
    __exc_tb: types.TracebackType | None,
) -> None: ...

# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def current_verifier() -> StrictVerifier: ...
def sandbox() -> SandboxContext: ...
def assert_interaction(source: Any, **expected: object) -> None: ...  # noqa: ANN401
def in_any_order() -> InAnyOrderContext: ...
def verify_all() -> None: ...

# ---------------------------------------------------------------------------
# Module-level factories
# ---------------------------------------------------------------------------

class _MockFactory:
    def __call__(self, path: str) -> ImportSiteMock: ...
    def object(self, target: object, attr: str) -> ObjectMock: ...

class _SpyFactory:
    def __call__(self, path: str) -> ImportSiteMock: ...
    def object(self, target: object, attr: str) -> ObjectMock: ...

mock: _MockFactory
spy: _SpyFactory

# ---------------------------------------------------------------------------
# Plugin proxy singletons
# ---------------------------------------------------------------------------

http: Any  # HttpPlugin proxy; typed as Any because httpx/requests are optional
subprocess_mock: Any  # SubprocessPlugin proxy
popen_mock: Any  # PopenPlugin proxy
smtp_mock: Any  # SmtpPlugin proxy
socket_mock: Any  # SocketPlugin proxy
db_mock: Any  # DatabasePlugin proxy
async_websocket_mock: Any  # AsyncWebSocketPlugin proxy
sync_websocket_mock: Any  # SyncWebSocketPlugin proxy
redis_mock: Any  # RedisPlugin proxy
mongo_mock: Any  # MongoPlugin proxy
dns_mock: Any  # DnsPlugin proxy
memcache_mock: Any  # MemcachePlugin proxy
celery_mock: Any  # CeleryPlugin proxy
log_mock: Any  # LoggingPlugin proxy
async_subprocess_mock: Any  # AsyncSubprocessPlugin proxy
psycopg2_mock: Any  # Psycopg2Plugin proxy
asyncpg_mock: Any  # AsyncpgPlugin proxy
boto3_mock: Any  # Boto3Plugin proxy
elasticsearch_mock: Any  # ElasticsearchPlugin proxy
jwt_mock: Any  # JwtPlugin proxy
crypto_mock: Any  # CryptoPlugin proxy
file_io_mock: Any  # FileIoPlugin proxy
pika_mock: Any  # PikaPlugin proxy
ssh_mock: Any  # SshPlugin proxy
grpc_mock: Any  # GrpcPlugin proxy
mcp_mock: Any  # McpPlugin proxy
native_mock: Any  # NativePlugin proxy
