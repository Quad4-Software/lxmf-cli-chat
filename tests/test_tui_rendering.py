import io
from unittest.mock import MagicMock, patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lxmf_cli_chat.main import LXMFChat


class MockSize:
    def __init__(self, columns, lines):
        self.columns = columns
        self.lines = lines


def get_mock_chat():
    with patch("RNS.Reticulum"), patch("LXMF.LXMRouter"):
        chat = LXMFChat()
        chat.source = MagicMock()
        chat.source.hash = b"test_hash"
        chat.messages = {}
        chat.collected_announces = {}
        return chat


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    cols=st.integers(min_value=40, max_value=200),
    lines=st.integers(min_value=10, max_value=60),
)
def test_refresh_ui_no_crash(cols, lines):
    mock_chat = get_mock_chat()
    mock_chat.get_term_size = MagicMock(return_value=MockSize(cols, lines))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        mock_chat.refresh_ui()

    output = captured_output.getvalue()
    assert "\033[2J" in output
    assert "LXMF CLI Chat" in output


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    cols=st.integers(min_value=110, max_value=200),
    lines=st.integers(min_value=20, max_value=40),
)
def test_side_panel_rendering(cols, lines):
    mock_chat = get_mock_chat()
    mock_chat.get_term_size = MagicMock(return_value=MockSize(cols, lines))
    mock_chat.ui_mode = "chat"
    mock_chat.collected_announces = {
        "hash1": {"name": "Peer1", "last_heard": 100, "hops": 1},
        "hash2": {"name": "Peer2", "last_heard": 200, "hops": 2},
    }

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        mock_chat.refresh_ui()

    output = captured_output.getvalue()
    assert "DISCOVERED" in output
    assert "Peer1" in output
    assert "Peer2" in output


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    cols=st.integers(min_value=60, max_value=120),
    lines=st.integers(min_value=20, max_value=40),
)
def test_wrapping_rendering(cols, lines):
    mock_chat = get_mock_chat()
    mock_chat.get_term_size = MagicMock(return_value=MockSize(cols, lines))
    target_hash = "abc"
    mock_chat.target_hash_hex = target_hash
    mock_chat.messages[target_hash] = [
        {
            "time": "12:00:00",
            "sender_name": "Alice",
            "content": "This is a message that should wrap.",
            "stamped": True,
        },
    ]

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        mock_chat.refresh_ui()

    output = captured_output.getvalue()
    assert "12:00:00" in output
    assert "Alice" in output
    assert "message" in output


def test_peer_mode_rendering():
    mock_chat = get_mock_chat()
    mock_chat.get_term_size = MagicMock(return_value=MockSize(80, 24))
    mock_chat.ui_mode = "peers"
    mock_chat.collected_announces = {
        "abc": {"name": "Alice", "last_heard": 1000, "hops": 1},
    }

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        mock_chat.refresh_ui()

    output = captured_output.getvalue()
    assert "Mode: PEERS" in output
    assert "Alice" in output
    assert "abc" in output
