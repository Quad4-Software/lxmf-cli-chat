import time
import tracemalloc
from unittest.mock import MagicMock, patch

from lxmf_cli_chat.main import LXMFChat


# Mocking for performance tests
class MockSize:
    def __init__(self, columns, lines):
        self.columns = columns
        self.lines = lines


def get_mock_chat():
    with patch("RNS.Reticulum"), patch("LXMF.LXMRouter"):
        chat = LXMFChat(
            headless=True,
        )  # Use headless to avoid TUI drawing overhead in logic tests
        chat.source = MagicMock()
        chat.source.hash = b"test_hash"
        return chat


def test_memory_usage_large_peer_list():
    tracemalloc.start()
    chat = get_mock_chat()

    # Simulate 10,000 peers
    for i in range(10000):
        h = f"{i:032x}"
        chat.collected_announces[h] = {
            "name": f"Peer_{i}",
            "last_heard": time.time(),
            "hops": 1,
        }

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak / 1024 / 1024 if peak is not None else 0
    print(f"\nPeak memory for 10k peers: {peak_mb:.2f} MB")

    # We expect 10k peers to take less than 10MB overhead
    assert peak_mb < 10.0


def test_rendering_speed_large_history():
    chat = get_mock_chat()
    chat.headless = False  # Enable rendering for this test
    chat.get_term_size = MagicMock(return_value=MockSize(80, 24))

    # Simulate 500 messages (our cache limit)
    target_hash = "0123456789abcdef0123456789abcdef"
    chat.target_hash_hex = target_hash
    chat.messages[target_hash] = []
    for i in range(500):
        chat.messages[target_hash].append(
            {
                "time": "12:00:00",
                "sender_name": f"User_{i}",
                "content": "This is a performance test message to check rendering speed.",
                "stamped": True,
            },
        )

    start_time = time.time()
    with patch("sys.stdout", MagicMock()):  # Don't actually write to terminal
        for _ in range(100):  # Redraw 100 times
            chat.refresh_ui()
    end_time = time.time()

    avg_render_time = (end_time - start_time) / 100 if end_time else 0
    print(f"\nAverage render time (500 msgs): {avg_render_time * 1000:.2f} ms")

    # Rendering should be very fast, ideally < 5ms
    assert avg_render_time < 0.010


def test_command_processing_latency():
    chat = get_mock_chat()

    # Add some background data
    for i in range(1000):
        chat.collected_announces[f"{i:032x}"] = {
            "name": f"P{i}",
            "last_heard": 0,
            "hops": 1,
        }

    start_time = time.time()
    for _ in range(1000):
        chat.handle_command("/t 000000")  # Resolve a short hash
    end_time = time.time()

    avg_latency = (end_time - start_time) / 1000 if end_time else 0
    print(f"\nAverage command latency: {avg_latency * 1000:.2f} ms")

    # Latency should be sub-millisecond
    assert avg_latency < 0.001
