"""
Exception hierarchy mirroring the e2b Python SDK exactly.
"""

from typing import Optional


class SandboxException(Exception):
    """Base class for all sandbox SDK exceptions."""

    def __init__(self, message: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class TimeoutException(SandboxException):
    """Raised when an operation exceeds its timeout."""


class InvalidArgumentException(SandboxException):
    """Raised when an invalid argument is supplied."""


class NotEnoughSpaceException(SandboxException):
    """Raised when there is not enough disk/memory space."""


class NotFoundException(SandboxException):
    """Raised when a requested resource is not found."""


class AuthenticationException(SandboxException):
    """Raised when API-key or access-token authentication fails."""


class TemplateException(SandboxException):
    """Raised when a sandbox template is invalid or unavailable."""


class RateLimitException(SandboxException):
    """Raised when the API rate limit is exceeded."""


class BuildException(SandboxException):
    """Raised when a template build operation fails."""


class FileUploadException(SandboxException):
    """Raised when a file upload fails."""


class CommandExitException(SandboxException):
    """Raised when a command exits with a non-zero exit code.

    Attributes:
        exit_code: The process exit code.
        stdout: Accumulated standard output.
        stderr: Accumulated standard error.
    """

    def __init__(
        self,
        message: str,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _raise_for_status(status_code: int, body: dict) -> None:
    """Inspect *status_code* and *body* and raise an appropriate exception.

    The *body* dict may contain a ``"message"`` or ``"error"`` key with a
    human-readable description from the server.
    """
    if status_code < 400:
        return

    message: str = (
        body.get("message")
        or body.get("error")
        or body.get("msg")
        or f"HTTP {status_code}"
    )

    if status_code == 401 or status_code == 403:
        raise AuthenticationException(message, status_code=status_code)
    if status_code == 404:
        raise NotFoundException(message, status_code=status_code)
    if status_code == 408 or status_code == 504:
        raise TimeoutException(message, status_code=status_code)
    if status_code == 422:
        raise InvalidArgumentException(message, status_code=status_code)
    if status_code == 429:
        raise RateLimitException(message, status_code=status_code)
    if status_code == 507:
        raise NotEnoughSpaceException(message, status_code=status_code)

    # Generic fallback
    raise SandboxException(message, status_code=status_code)
