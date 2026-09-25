"""Operation results that keep "command sent" separate from "TV state confirmed".

A `CommandResult` only exists when the command was *sent*: a command that could not be
delivered raises a `ControlError` instead. Whether the TV then reached the requested state is
`confirmation`, and `CONFIRMED` requires a status that was actually observed on the TV.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from control_tv.domain.errors import InvalidArgumentError
from control_tv.domain.models import ConnectionState, DeviceId, DeviceStatus


class Command(StrEnum):
    LOAD_MEDIA = "load_media"
    PLAY = "play"
    PAUSE = "pause"
    STOP = "stop"
    SEEK = "seek"
    SET_VOLUME = "set_volume"
    SET_MUTED = "set_muted"


class Confirmation(StrEnum):
    NOT_CHECKED = "not_checked"
    """Sent; no attempt was made to verify the TV state."""

    UNCONFIRMED = "unconfirmed"
    """Sent; the TV state did not show the expected result in the time allowed."""

    CONFIRMED = "confirmed"
    """Sent; a status observed on the TV shows the expected result."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandResult:
    command: Command
    device_id: DeviceId
    confirmation: Confirmation
    observed: DeviceStatus | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.observed is not None and self.observed.device_id != self.device_id:
            raise InvalidArgumentError(
                "observed status belongs to a different device", device_id=self.device_id
            )
        if self.confirmation is Confirmation.CONFIRMED and (
            self.observed is None or self.observed.connection is not ConnectionState.CONNECTED
        ):
            raise InvalidArgumentError(
                "a confirmed result needs a status observed on a connected device",
                device_id=self.device_id,
            )

    @property
    def confirmed(self) -> bool:
        return self.confirmation is Confirmation.CONFIRMED
