"""Explicit control errors.

A failed operation raises a `ControlError`; it is never reported as a successful result.
Each concrete error has a stable machine-readable `code` so that adapters (UI, MCP) can map
failures to actionable messages without parsing text.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "invalid_argument"
    DEVICE_NOT_FOUND = "device_not_found"
    AMBIGUOUS_TARGET = "ambiguous_target"
    DEVICE_UNAVAILABLE = "device_unavailable"
    DISCOVERY_FAILED = "discovery_failed"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    UNSUPPORTED_MEDIA = "unsupported_media"
    COMMAND_REJECTED = "command_rejected"
    TIMEOUT = "timeout"


class ControlError(Exception):
    """Base class for every failure raised by the control layer."""

    code: ClassVar[ErrorCode]

    def __init__(self, message: str, *, device_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.device_id = device_id


class InvalidArgumentError(ControlError):
    """A caller-supplied value is malformed or out of range."""

    code = ErrorCode.INVALID_ARGUMENT


class DeviceNotFoundError(ControlError):
    """No known device matches the requested identifier."""

    code = ErrorCode.DEVICE_NOT_FOUND


class AmbiguousTargetError(ControlError):
    """The requested target matches several devices and cannot be resolved safely."""

    code = ErrorCode.AMBIGUOUS_TARGET


class DeviceUnavailableError(ControlError):
    """The device is known but cannot be reached or is not connected."""

    code = ErrorCode.DEVICE_UNAVAILABLE


class DiscoveryError(ControlError):
    """Device discovery could not run or failed."""

    code = ErrorCode.DISCOVERY_FAILED


class UnsupportedOperationError(ControlError):
    """The device or its current session does not support the requested operation."""

    code = ErrorCode.UNSUPPORTED_OPERATION


class UnsupportedMediaError(ControlError):
    """The requested media cannot be played by the target."""

    code = ErrorCode.UNSUPPORTED_MEDIA


class CommandRejectedError(ControlError):
    """The receiver refused a command that was delivered to it."""

    code = ErrorCode.COMMAND_REJECTED


class OperationTimeoutError(ControlError):
    """An operation exceeded its bounded time budget."""

    code = ErrorCode.TIMEOUT
