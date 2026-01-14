import os
import sys
from unittest.mock import MagicMock, patch

import atheris

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

with atheris.instrument_imports():
    from lxmf_cli_chat.main import LXMFChat


def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    input_str = fdp.ConsumeUnicodeNoSurrogates(1024)

    # Mocking dependencies to avoid disk/network IO during fuzzing
    with (
        patch("RNS.Reticulum"),
        patch("LXMF.LXMRouter"),
        patch("os.makedirs"),
        patch("os.path.exists", return_value=True),
    ):
        chat = LXMFChat(config_path="/tmp/fuzz_config")
        chat.send_message = MagicMock()  # Avoid further side effects

        try:
            chat.handle_command(input_str)
        except Exception:
            # We are looking for crashes that aren't caught exceptions
            pass


def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
