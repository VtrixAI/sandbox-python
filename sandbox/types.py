from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterator, List, Literal, Optional, Union, IO


@dataclass
class SandboxInfo:
    """Metadata returned by the management API for a sandbox instance."""
    sandbox_id: str
    template_id: Optional[str] = None
    alias: Optional[str] = None
    started_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    state: str = ""


@dataclass
class EntryInfo:
    """Metadata about a single filesystem entry (file or directory)."""
    name: str
    type: str
    path: str
    size: int = 0
    mode: int = 0
    permissions: str = ""
    owner: str = ""
    group: str = ""
    modified_time: Optional[datetime] = None
    symlink_target: Optional[str] = None


@dataclass
class WriteInfo:
    """Result returned after a successful file write."""
    name: str
    path: str
    type: Optional[str] = None


@dataclass
class WriteEntry:
    """A single file entry used in batch-write operations."""
    path: str
    data: Union[str, bytes]


@dataclass
class ProcessInfo:
    """Information about a running or finished process inside the sandbox."""
    pid: int
    cmd: str
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    envs: Dict[str, str] = field(default_factory=dict)
    tag: Optional[str] = None


@dataclass
class CommandResult:
    """The result of a completed command execution."""
    stdout: str
    stderr: str
    exit_code: int = 0
    error: Optional[str] = None


@dataclass
class FilesystemEvent:
    """A single event emitted by a directory watcher."""
    name: str
    type: str


@dataclass
class PtySize:
    """Terminal dimensions for a PTY process."""
    rows: int
    cols: int


@dataclass
class ConnectionConfig:
    """All connection parameters needed by the SDK internals."""
    sandbox_id: str
    envd_url: str
    access_token: Optional[str]
    api_key: Optional[str]
    base_url: str
    request_timeout: float = 30.0
