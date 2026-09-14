from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrated import cases


def test_create_persists_open_case_for_tenant() -> None:
    cursor = MagicMock()
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    with patch("integrated.cases.connect", return_value=connection):
        result = cases.create("tenant-a", "user-a", "Need help", "Details")
    assert result["case_id"].startswith("case-")
    assert result["status"] == "open"
    assert cursor.execute.call_args.args[1][1] == "tenant-a"


def test_transition_rejects_invalid_status() -> None:
    try:
        cases.transition("tenant-a", "case-a", "closed")
    except ValueError as error:
        assert str(error) == "invalid_case_status"
    else:
        raise AssertionError("invalid case status was accepted")
