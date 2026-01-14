import os
import shutil
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lxmf_cli_chat.main import LXMFChat

# Setup a temporary config directory for tests
TEST_CONFIG_DIR = "/tmp/lxmf_test_config"


@pytest.fixture(autouse=True)
def setup_teardown():
    if os.path.exists(TEST_CONFIG_DIR):
        shutil.rmtree(TEST_CONFIG_DIR)
    os.makedirs(TEST_CONFIG_DIR)
    yield
    if os.path.exists(TEST_CONFIG_DIR):
        shutil.rmtree(TEST_CONFIG_DIR)


class MockReticulum:
    def __init__(self, configdir=None):
        pass


class MockLXMRouter:
    def __init__(self, storagepath=None):
        self.pending_outbound = []

    def register_delivery_identity(self, identity, display_name=None, stamp_cost=None):
        mock_dest = MagicMock()
        mock_dest.hash = b"dummy_hash"
        return mock_dest

    def register_delivery_callback(self, callback):
        pass

    def announce(self, hash):
        pass

    def set_outbound_propagation_node(self, node):
        pass

    def request_messages_from_propagation_node(self, identity):
        pass


@patch("RNS.Reticulum", side_effect=MockReticulum)
@patch("LXMF.LXMRouter", side_effect=MockLXMRouter)
def test_initialization(mock_router, mock_rns):
    chat = LXMFChat(config_path=TEST_CONFIG_DIR, display_name="TestUser", debug=False)
    assert chat.display_name == "TestUser"
    assert chat.ui_mode == "chat"
    assert chat.running is True


@patch("RNS.Reticulum", side_effect=MockReticulum)
@patch("LXMF.LXMRouter", side_effect=MockLXMRouter)
@given(
    name=st.text(min_size=1, max_size=20)
    .map(str.strip)
    .filter(lambda x: len(x) > 0 and not x.startswith("/")),
)
def test_handle_name_command(mock_router, mock_rns, name):
    chat = LXMFChat(config_path=TEST_CONFIG_DIR, debug=False)
    chat.handle_command(f"/name {name}")
    assert chat.display_name == name
    assert "Name changed to" in chat.status_msg


@patch("RNS.Reticulum", side_effect=MockReticulum)
@patch("LXMF.LXMRouter", side_effect=MockLXMRouter)
@given(target=st.from_regex(r"^[0-9a-f]{32}$").map(str.strip))
def test_handle_target_command(mock_router, mock_rns, target):
    chat = LXMFChat(config_path=TEST_CONFIG_DIR, debug=False)
    chat.handle_command(f"/target {target}")
    assert chat.target_hash_hex == target
    assert "Target:" in chat.status_msg


@patch("RNS.Reticulum", side_effect=MockReticulum)
@patch("LXMF.LXMRouter", side_effect=MockLXMRouter)
@given(cmd=st.text(min_size=1, max_size=100).filter(lambda x: not x.startswith("/")))
def test_handle_message_input(mock_router, mock_rns, cmd):
    chat = LXMFChat(config_path=TEST_CONFIG_DIR, debug=False)
    chat.target_hash_hex = "0123456789abcdef0123456789abcdef"

    # Mock send_message to avoid network calls
    chat.send_message = MagicMock()

    chat.handle_command(cmd)
    chat.send_message.assert_called_with(chat.target_hash_hex, cmd)


@patch("RNS.Reticulum", side_effect=MockReticulum)
@patch("LXMF.LXMRouter", side_effect=MockLXMRouter)
def test_handle_short_hash_target(mock_router, mock_rns):
    chat = LXMFChat(config_path=TEST_CONFIG_DIR, debug=False)
    full_hash = "abcdef1234567890abcdef1234567890"
    chat.collected_announces[full_hash] = {"name": "Alice", "last_heard": 0, "hops": 1}

    chat.handle_command("/target abcd")
    assert chat.target_hash_hex == full_hash
    assert "Alice" in chat.status_msg
    assert full_hash in chat.status_msg


@patch("RNS.Reticulum", side_effect=MockReticulum)
@patch("LXMF.LXMRouter", side_effect=MockLXMRouter)
def test_handle_ambiguous_short_hash(mock_router, mock_rns):
    chat = LXMFChat(config_path=TEST_CONFIG_DIR, debug=False)
    hash1 = "abcdef1234567890abcdef1234567891"
    hash2 = "abcdef1234567890abcdef1234567892"
    chat.collected_announces[hash1] = {"name": "Alice", "last_heard": 0, "hops": 1}
    chat.collected_announces[hash2] = {"name": "Bob", "last_heard": 0, "hops": 1}

    chat.handle_command("/target abcd")
    assert chat.target_hash_hex == ""
    assert "Ambiguous" in chat.status_msg


@patch("RNS.Reticulum", side_effect=MockReticulum)
@patch("LXMF.LXMRouter", side_effect=MockLXMRouter)
def test_quit_command(mock_router, mock_rns):
    chat = LXMFChat(config_path=TEST_CONFIG_DIR, debug=False)
    chat.handle_command("/quit")
    assert chat.running is False
