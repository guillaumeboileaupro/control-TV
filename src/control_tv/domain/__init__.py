"""Shared control/domain vocabulary used by every adapter (manual UI, MCP).

This package is pure Python: it must not import a Cast library, a UI toolkit or MCP.
"""

from control_tv.domain.errors import (
    AmbiguousTargetError,
    CommandRejectedError,
    ControlError,
    DeviceNotFoundError,
    DeviceUnavailableError,
    DiscoveryError,
    ErrorCode,
    InvalidArgumentError,
    OperationTimeoutError,
    UnsupportedMediaError,
    UnsupportedOperationError,
)
from control_tv.domain.models import (
    ConnectionState,
    Device,
    DeviceId,
    DeviceKind,
    DeviceStatus,
    MediaRequest,
    MediaStatus,
    PlaybackState,
    ReceiverStatus,
)
from control_tv.domain.results import Command, CommandResult, Confirmation

__all__ = [
    "AmbiguousTargetError",
    "Command",
    "CommandRejectedError",
    "CommandResult",
    "Confirmation",
    "ConnectionState",
    "ControlError",
    "Device",
    "DeviceId",
    "DeviceKind",
    "DeviceNotFoundError",
    "DeviceStatus",
    "DeviceUnavailableError",
    "DiscoveryError",
    "ErrorCode",
    "InvalidArgumentError",
    "MediaRequest",
    "MediaStatus",
    "OperationTimeoutError",
    "PlaybackState",
    "ReceiverStatus",
    "UnsupportedMediaError",
    "UnsupportedOperationError",
]
