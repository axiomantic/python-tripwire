"""Protocol-typed firewall request classes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FirewallRequest:
    """Abstract base for all firewall requests.

    Every subclass MUST set ``protocol`` as a class-level string constant.
    This is used by M() to filter rules by protocol before field matching.
    """
    protocol: str


# ---------------------------------------------------------------------------
# Network base
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NetworkFirewallRequest(FirewallRequest):
    """Base for protocols that connect to host:port."""
    host: str = ""
    port: int = 0


# ---------------------------------------------------------------------------
# Protocol-specific subclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HttpFirewallRequest(NetworkFirewallRequest):
    protocol: str = "http"  # always "http" regardless of scheme
    scheme: str = ""        # "http" or "https"
    path: str = ""
    method: str = ""

@dataclass(frozen=True, slots=True)
class RedisFirewallRequest(NetworkFirewallRequest):
    protocol: str = "redis"
    db: int = 0
    command: str = ""

@dataclass(frozen=True, slots=True)
class PostgresFirewallRequest(NetworkFirewallRequest):
    """Covers both psycopg2 and asyncpg. protocol is "psycopg2" or "asyncpg"."""
    dbname: str = ""

@dataclass(frozen=True, slots=True)
class SocketFirewallRequest(NetworkFirewallRequest):
    protocol: str = "socket"
    family: str = ""  # "AF_INET", "AF_INET6", "AF_UNIX"

@dataclass(frozen=True, slots=True)
class DnsFirewallRequest(FirewallRequest):
    protocol: str = "dns"
    hostname: str = ""
    port: int = 0
    rdtype: str = ""  # "A", "AAAA", "CNAME", etc.

@dataclass(frozen=True, slots=True)
class SmtpFirewallRequest(NetworkFirewallRequest):
    protocol: str = "smtp"

@dataclass(frozen=True, slots=True)
class SshFirewallRequest(NetworkFirewallRequest):
    protocol: str = "ssh"
    username: str = ""

@dataclass(frozen=True, slots=True)
class GrpcFirewallRequest(NetworkFirewallRequest):
    protocol: str = "grpc"
    method: str = ""     # full method path, e.g. "/pkg.Service/Method"
    call_type: str = ""  # "unary", "server_streaming", etc.

@dataclass(frozen=True, slots=True)
class PikaFirewallRequest(NetworkFirewallRequest):
    protocol: str = "pika"
    virtual_host: str = ""
    exchange: str = ""
    routing_key: str = ""

@dataclass(frozen=True, slots=True)
class ElasticsearchFirewallRequest(NetworkFirewallRequest):
    protocol: str = "elasticsearch"
    index: str = ""
    operation: str = ""  # "search", "index", "delete", etc.

@dataclass(frozen=True, slots=True)
class MongoFirewallRequest(NetworkFirewallRequest):
    protocol: str = "mongo"
    database: str = ""
    collection: str = ""
    operation: str = ""  # "find", "insert_one", etc.

@dataclass(frozen=True, slots=True)
class MemcacheFirewallRequest(NetworkFirewallRequest):
    protocol: str = "memcache"
    command: str = ""  # "get", "set", "delete", etc.

@dataclass(frozen=True, slots=True)
class WebSocketFirewallRequest(NetworkFirewallRequest):
    """Constructed by parsing the WebSocket URI."""
    protocol: str = "websocket"
    scheme: str = ""   # "ws" or "wss"
    path: str = ""

@dataclass(frozen=True, slots=True)
class Boto3FirewallRequest(FirewallRequest):
    """No host -- AWS manages endpoints."""
    protocol: str = "boto3"
    service: str = ""     # "s3", "dynamodb", etc.
    operation: str = ""   # "PutObject", "GetItem", etc.

@dataclass(frozen=True, slots=True)
class McpFirewallRequest(FirewallRequest):
    protocol: str = "mcp"
    tool_name: str = ""
    uri: str = ""  # resource or prompt URI

# ---------------------------------------------------------------------------
# Non-network protocols
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SubprocessFirewallRequest(FirewallRequest):
    protocol: str = "subprocess"
    command: str = ""   # full command string (joined args)
    binary: str = ""    # executable name (e.g., "git", "curl")

@dataclass(frozen=True, slots=True)
class FileIoFirewallRequest(FirewallRequest):
    protocol: str = "file_io"
    path: str = ""
    operation: str = ""  # "read", "write", "delete", "stat", etc.
    mode: str = ""       # file mode string if applicable

@dataclass(frozen=True, slots=True)
class DatabaseFirewallRequest(FirewallRequest):
    """For sqlite (DatabasePlugin)."""
    protocol: str = "database"
    database_path: str = ""
