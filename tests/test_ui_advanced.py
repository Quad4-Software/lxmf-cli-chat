import io
import time
from unittest.mock import MagicMock, patch

from lxmf_cli_chat.main import LXMFChat


class MockSize:
    def __init__(self, columns, lines):
        self.columns = columns
        self.lines = lines


def get_mock_chat():
    with patch("RNS.Reticulum"), patch("LXMF.LXMRouter"):
        chat = LXMFChat(headless=False)
        chat.source = MagicMock()
        chat.source.hash = b"test_hash_12345"
        chat.messages = {}
        chat.collected_announces = {}
        chat.active_sessions = []
        return chat


def test_ui_mode_switching():
    chat = get_mock_chat()
    chat.get_term_size = MagicMock(return_value=MockSize(80, 24))

    # Initial mode
    assert chat.ui_mode == "chat"

    # Add a peer so we can switch to peers mode
    chat.collected_announces = {
        "some_hash": {"name": "Some Peer", "last_heard": 100, "hops": 1}
    }

    # Switch to peers
    chat.handle_command("/peers")
    assert chat.ui_mode == "peers"

    # Switch back to chat via Esc (simulated by handle_command or direct state change)
    chat.ui_mode = "chat"
    assert chat.ui_mode == "chat"


def test_command_status_messages():
    chat = get_mock_chat()
    chat.get_term_size = MagicMock(return_value=MockSize(80, 24))

    chat.handle_command("/mode direct")
    assert chat.send_mode == "direct"
    assert "Send mode set to DIRECT" in chat.status_msg

    chat.handle_command("/name NewName")
    assert chat.display_name == "NewName"
    assert "Name changed to NewName" in chat.status_msg


def test_message_arrival_refresh():
    chat = get_mock_chat()
    chat.get_term_size = MagicMock(return_value=MockSize(80, 24))
    chat.refresh_ui = MagicMock()

    # Mock an incoming message
    mock_lxm = MagicMock()
    mock_lxm.source_hash = b"\x01" * 16
    mock_lxm.content_as_string.return_value = "Hello World"
    mock_lxm.timestamp = time.time()
    mock_lxm.stamp_valid = True
    mock_lxm.signature_validated = True

    chat.on_message(mock_lxm)

    sender_hash = "01" * 16
    assert sender_hash in chat.messages
    assert chat.messages[sender_hash][0]["content"] == "Hello World"
    assert chat.active_sessions[0] == sender_hash
    chat.refresh_ui.assert_called()


def test_scrolling_logic():
    chat = get_mock_chat()
    chat.ui_mode = "chat"
    chat.target_hash_hex = "target"
    chat.messages["target"] = [
        {"content": f"msg {i}", "sender_name": "Alice"} for i in range(50)
    ]

    # Mock height to be small
    chat.get_term_size = MagicMock(return_value=MockSize(80, 10))
    # msg_area_height = 10 - 4 = 6

    # Initial scroll should be 0 (bottom)
    assert chat.chat_scroll == 0

    # Scroll up (simulated by key press logic usually, but we can test the state)
    chat.chat_scroll = 10

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        chat.refresh_ui()

    output = captured_output.getvalue()
    # Should see some of the messages
    assert "msg" in output


def test_pn_commands():
    chat = get_mock_chat()
    chat.handle_command("/pn auto")
    assert chat.auto_pn is True

    chat.handle_command("/pn abcdef0123456789")
    assert chat.auto_pn is False
    assert (
        chat.manual_pn == b"\xab\xcd\xef\x01#Eg\x89"
    )  # bytes.fromhex("abcdef0123456789")


def test_block_unblock():
    chat = get_mock_chat()
    target = "01" * 16
    chat.handle_command(f"/block {target}")
    assert target in chat.blocked_addresses

    chat.handle_command(f"/unblock {target}")
    assert target not in chat.blocked_addresses


def test_peer_selection_in_ui():
    chat = get_mock_chat()
    chat.ui_mode = "peers"
    chat.collected_announces = {
        "hash1": {"name": "Alice", "last_heard": 100, "hops": 1},
        "hash2": {"name": "Bob", "last_heard": 200, "hops": 2},
    }
    # Sorted by last_heard reverse: hash2, hash1
    chat.selected_peer_idx = 0  # Should be Bob

    # We can't easily call run_unix/run_windows, but we can test the selection logic
    # if it were extracted. Since it's inside run_unix, we might want to refactor
    # or just test the state transformation if we could.
    # For now, let's just ensure refresh_ui doesn't crash with peers.
    chat.get_term_size = MagicMock(return_value=MockSize(80, 24))
    chat.refresh_ui()
