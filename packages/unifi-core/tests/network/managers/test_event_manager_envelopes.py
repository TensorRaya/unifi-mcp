"""The Network EventManager tells a malformed response apart from a valid empty collection."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unifi_core.exceptions import UniFiMalformedResponseError
from unifi_core.incident_evidence import FailureKind, failure_from_exception
from unifi_core.network.managers.event_manager import EventManager


def _manager(use_v2: bool, response) -> EventManager:
    connection = MagicMock()
    connection.site = "default"
    connection.request = AsyncMock(return_value=response)
    manager = EventManager(connection)
    manager._use_v2 = use_v2
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_v2", "response"),
    [
        (True, {"data": []}),
        (True, {"logs": []}),
        (True, [{"data": [], "total_element_count": 0}]),
        (True, [{"logs": [], "total_page_count": 0}]),
        (True, [{"total_element_count": 0, "total_page_count": 0}]),
        (True, []),
        (False, []),
        (False, {"data": []}),
    ],
)
async def test_valid_empty_collections_are_empty(use_v2, response) -> None:
    manager = _manager(use_v2, response)
    if use_v2 and response == []:
        # Only /system-log/critical answers an empty result this way (see below); events keep raising.
        assert await manager.get_alarms(limit=10) == []
        return
    assert await manager.get_events(limit=10) == []
    assert await manager.get_alarms(limit=10) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_v2", "response"),
    [
        (True, {"unexpected": "envelope"}),
        (True, [{"unexpected": "envelope"}]),
        (True, [{"data": []}, {"data": []}]),
        (True, [{"total_element_count": 3}]),
        (True, {"data": "invalid"}),
        (True, {"logs": {"not": "a list"}}),
        (True, "not json"),
        (True, None),
        (False, {"meta": {"rc": "ok"}}),
        (False, {"data": "invalid"}),
        (False, None),
    ],
)
async def test_malformed_envelopes_raise_instead_of_returning_empty(use_v2, response) -> None:
    manager = _manager(use_v2, response)
    with pytest.raises(UniFiMalformedResponseError) as events_error:
        await manager.get_events(limit=10)
    with pytest.raises(UniFiMalformedResponseError):
        await manager.get_alarms(limit=10)
    # Fixed operation context only; the payload is never echoed.
    assert "invalid" not in str(events_error.value) and "unexpected" not in str(events_error.value)
    assert failure_from_exception(events_error.value).kind is FailureKind.PARSE_FAILED


@pytest.mark.asyncio
async def test_malformed_later_v2_page_raises() -> None:
    manager = _manager(True, None)
    manager._connection.request.side_effect = [{"data": [{"id": f"e{i}"} for i in range(100)]}, {"oops": 1}]
    with pytest.raises(UniFiMalformedResponseError):
        await manager.get_events(limit=150)


@pytest.mark.asyncio
async def test_bare_empty_list_is_no_alarms_but_still_malformed_for_events() -> None:
    """Network 10.6 answers POST /system-log/critical with HTTP 200 and a bare ``[]`` when nothing is
    critical, for every request body, while /system-log/all answers the same window with a full
    envelope. ``[]`` is the empty alarm list there, nothing else; any non-empty bare list stays
    malformed, and so does a bare ``[]`` from the events view."""
    manager = _manager(True, [])
    assert await manager.get_alarms(limit=10) == []
    page = await manager.get_alarms_page(limit=10)
    assert page.rows == [] and page.has_more is False and page.total_reported == 0
    with pytest.raises(UniFiMalformedResponseError):
        await manager.get_events(limit=10)
    for bare_rows in ([{"id": "a1"}], [[]]):
        with pytest.raises(UniFiMalformedResponseError):
            await _manager(True, bare_rows).get_alarms(limit=10)
