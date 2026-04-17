"""
sandbox – e2b-compatible sandbox SDK for nano-executor via hermes gateway.

Quick start::

    from sandbox import Sandbox

    sb = Sandbox.create(template="base", api_key="sk-...", base_url="http://localhost:8080")
    result = sb.commands.run("echo hello")
    print(result.stdout)   # "hello\\n"
    sb.kill()

Async::

    from sandbox import AsyncSandbox

    async def main():
        sb = await AsyncSandbox.create(template="base")
        result = await sb.commands.run("echo hello")
        await sb.kill()
"""

from .sandbox import Sandbox, AsyncSandbox
from .filesystem import Filesystem, AsyncFilesystem
from .commands import Commands, AsyncCommands, Pty, AsyncPty, CommandHandle, AsyncCommandHandle
from .templates import TemplateClient, AsyncTemplateClient
from .exceptions import (
    SandboxException,
    TimeoutException,
    InvalidArgumentException,
    NotEnoughSpaceException,
    NotFoundException,
    AuthenticationException,
    TemplateException,
    RateLimitException,
    BuildException,
    FileUploadException,
    CommandExitException,
    _raise_for_status,
)
from .types import (
    SandboxInfo,
    EntryInfo,
    WriteInfo,
    WriteEntry,
    ProcessInfo,
    CommandResult,
    FilesystemEvent,
    PtySize,
    ConnectionConfig,
)

__all__ = [
    "Sandbox",
    "AsyncSandbox",
    "Filesystem",
    "AsyncFilesystem",
    "Commands",
    "AsyncCommands",
    "Pty",
    "AsyncPty",
    "CommandHandle",
    "AsyncCommandHandle",
    "TemplateClient",
    "AsyncTemplateClient",
    "SandboxException",
    "TimeoutException",
    "InvalidArgumentException",
    "NotEnoughSpaceException",
    "NotFoundException",
    "AuthenticationException",
    "TemplateException",
    "RateLimitException",
    "BuildException",
    "FileUploadException",
    "CommandExitException",
    "_raise_for_status",
    "SandboxInfo",
    "EntryInfo",
    "WriteInfo",
    "WriteEntry",
    "ProcessInfo",
    "CommandResult",
    "FilesystemEvent",
    "PtySize",
    "ConnectionConfig",
]
