from __future__ import annotations

import pytest

from control_tv.domain import (
    CommandRejectedError,
    ControlError,
    DeviceUnavailableError,
    ErrorCode,
)


def concrete_errors() -> list[type[ControlError]]:
    return ControlError.__subclasses__()


def test_every_error_code_has_exactly_one_error_class() -> None:
    codes = [error.code for error in concrete_errors()]

    assert sorted(codes) == sorted(ErrorCode)
    assert len(set(codes)) == len(codes)


@pytest.mark.parametrize("error_type", concrete_errors(), ids=lambda t: t.__name__)
def test_errors_expose_message_device_and_stable_code(error_type: type[ControlError]) -> None:
    error = error_type("TV did not answer", device_id="uuid-1")

    assert str(error) == "TV did not answer"
    assert error.message == "TV did not answer"
    assert error.device_id == "uuid-1"
    assert isinstance(error.code, ErrorCode)


def test_device_is_optional() -> None:
    assert DeviceUnavailableError("no route").device_id is None


def test_errors_can_be_handled_through_the_base_class() -> None:
    with pytest.raises(ControlError) as excinfo:
        raise CommandRejectedError("receiver refused pause", device_id="uuid-1")

    assert excinfo.value.code is ErrorCode.COMMAND_REJECTED
    assert excinfo.value.code.value == "command_rejected"
