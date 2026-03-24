"""Type stubs for bigfoot's dynamic module-level API.

Enables Pyright/mypy to resolve:
- ``with bigfoot:`` context manager protocol
- Module-level functions (current_verifier, sandbox, assert_interaction, etc.)
- Module-level factories (mock, spy)
- Plugin proxy attributes (http, subprocess_mock, etc.)
- All error classes
"""

from __future__ import annotations

import types
from typing import Any

from bigfoot._base_plugin import BasePlugin as BasePlugin
from bigfoot._context import GuardPassThrough as GuardPassThrough
from bigfoot._context import get_verifier_or_raise as get_verifier_or_raise
from bigfoot._errors import AllWildcardAssertionError as AllWildcardAssertionError
from bigfoot._errors import AssertionInsideSandboxError as AssertionInsideSandboxError
from bigfoot._errors import AutoAssertError as AutoAssertError
from bigfoot._errors import BigfootConfigError as BigfootConfigError
from bigfoot._errors import BigfootError as BigfootError
from bigfoot._errors import ConflictError as ConflictError
from bigfoot._errors import GuardedCallError as GuardedCallError
from bigfoot._errors import GuardedCallWarning as GuardedCallWarning
from bigfoot._errors import InteractionMismatchError as InteractionMismatchError
from bigfoot._errors import InvalidStateError as InvalidStateError
from bigfoot._errors import MissingAssertionFieldsError as MissingAssertionFieldsError
from bigfoot._errors import NoActiveVerifierError as NoActiveVerifierError
from bigfoot._errors import SandboxNotActiveError as SandboxNotActiveError
from bigfoot._errors import UnassertedInteractionsError as UnassertedInteractionsError
from bigfoot._errors import UnmockedInteractionError as UnmockedInteractionError
from bigfoot._errors import UnusedMocksError as UnusedMocksError
from bigfoot._errors import VerificationError as VerificationError
from bigfoot._guard import allow as allow
from bigfoot._guard import deny as deny
from bigfoot._mock_plugin import ImportSiteMock, ObjectMock
from bigfoot._mock_plugin import MockPlugin as MockPlugin
from bigfoot._registry import GUARD_ELIGIBLE_PREFIXES as GUARD_ELIGIBLE_PREFIXES
from bigfoot._registry import PluginEntry as PluginEntry
from bigfoot._timeline import Interaction as Interaction
from bigfoot._timeline import Timeline as Timeline
from bigfoot._verifier import InAnyOrderContext as InAnyOrderContext
from bigfoot._verifier import SandboxContext as SandboxContext
from bigfoot._verifier import StrictVerifier as StrictVerifier
from bigfoot.plugins.async_subprocess_plugin import (
    AsyncSubprocessPlugin as AsyncSubprocessPlugin,
)
from bigfoot.plugins.database_plugin import DatabasePlugin as DatabasePlugin
from bigfoot.plugins.dns_plugin import DnsPlugin as DnsPlugin
from bigfoot.plugins.file_io_plugin import FileIoPlugin as FileIoPlugin
from bigfoot.plugins.logging_plugin import LoggingPlugin as LoggingPlugin
from bigfoot.plugins.memcache_plugin import MemcachePlugin as MemcachePlugin
from bigfoot.plugins.native_plugin import NativePlugin as NativePlugin
from bigfoot.plugins.popen_plugin import PopenPlugin as PopenPlugin
from bigfoot.plugins.redis_plugin import RedisPlugin as RedisPlugin
from bigfoot.plugins.smtp_plugin import SmtpPlugin as SmtpPlugin
from bigfoot.plugins.socket_plugin import SocketPlugin as SocketPlugin
from bigfoot.plugins.subprocess import SubprocessPlugin as SubprocessPlugin
from bigfoot.plugins.websocket_plugin import (
    AsyncWebSocketPlugin as AsyncWebSocketPlugin,
)
from bigfoot.plugins.websocket_plugin import (
    SyncWebSocketPlugin as SyncWebSocketPlugin,
)

# Optional plugin classes (may not be importable if extras not installed)
try:
    from bigfoot.plugins.http import HttpPlugin as HttpPlugin
except ImportError: ...

try:
    from bigfoot.plugins.celery_plugin import CeleryPlugin as CeleryPlugin
except ImportError: ...

try:
    from bigfoot.plugins.boto3_plugin import Boto3Plugin as Boto3Plugin
except ImportError: ...

try:
    from bigfoot.plugins.elasticsearch_plugin import (
        ElasticsearchPlugin as ElasticsearchPlugin,
    )
except ImportError: ...

try:
    from bigfoot.plugins.jwt_plugin import JwtPlugin as JwtPlugin
except ImportError: ...

try:
    from bigfoot.plugins.crypto_plugin import CryptoPlugin as CryptoPlugin
except ImportError: ...

try:
    from bigfoot.plugins.mongo_plugin import MongoPlugin as MongoPlugin
except ImportError: ...

try:
    from bigfoot.plugins.pika_plugin import PikaPlugin as PikaPlugin
except ImportError: ...

try:
    from bigfoot.plugins.ssh_plugin import SshPlugin as SshPlugin
except ImportError: ...

try:
    from bigfoot.plugins.grpc_plugin import GrpcPlugin as GrpcPlugin
except ImportError: ...

try:
    from bigfoot.plugins.mcp_plugin import McpPlugin as McpPlugin
except ImportError: ...

try:
    from bigfoot.plugins.psycopg2_plugin import Psycopg2Plugin as Psycopg2Plugin
except ImportError: ...

try:
    from bigfoot.plugins.asyncpg_plugin import AsyncpgPlugin as AsyncpgPlugin
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
