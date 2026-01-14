# LXMF CLI Chat

Basic Ephemeral TUI and CLI Client for LXMF.

Ephemeral as in chat sessions and announces are ephemeral/not stored, identity is preserved. 

Only dependencies are RNS and LXMF.

![showcase image 2026](https://git.quad4.io/RNS-Things/LXMF-CLI-Chat/raw/commit/793c857384d0726a3e643b40bbdb00aff34badd0/showcase/2026-01-14_11-05.png)

## Installation

```bash
# pip w/ git
pip install git+https://git.quad4.io/RNS-Things/LXMF-CLI-Chat.git

# pipx w/ git
pipx install git+https://git.quad4.io/RNS-Things/LXMF-CLI-Chat.git

# poetry
git clone https://git.quad4.io/RNS-Things/LXMF-CLI-Chat.git
poetry install
```

## Usage

### Interactive TUI

```bash
lxmf-chat --name "MyName"
```

### Headless / CLI Sending

Send a single message without entering the TUI:

```bash
lxmf-chat --send-to 7cc8d66b4f6a0e0e49d34af7f6077b5a --msg "Hello from CLI"
```

## Shortcuts & Commands

### Commands
- `/t` | `/target <hex>`: Set destination hash
- `/c` | `/chat [idx]`: Switch between active chats (or cycle if no index)
- `/r` | `/reply <msg>`: Send a message to the most recent conversation
- `/s` | `/search <query>`: Search discovered peers
- `/p` | `/peers`: Open peer list browser
- `/n` | `/name <name>`: Change your display name
- `/a` | `/announce`: Send a network announce
- `/id`: Show your own destination hash
- `/manual` | `/help`: Show the command manual
- `/q` | `/quit`: Exit
- `/mode <auto|direct|propagated>`: Change LXMF delivery method
- `/stamp <cost>`: Set your required inbound stamp cost (1-255)
- `/grant [hex]` | `/ticket`: Send a message and grant a one-time ticket
- `/block <hex>`: Block a destination hash
- `/unblock <hex>`: Unblock a destination hash
- `/blocked`: List blocked addresses

### TUI Shortcuts
- **Ctrl-P**: Toggle peer list mode
- **Ctrl-N**: Cycle active chats
- **Ctrl-A**: Trigger announce
- **Ctrl-L**: Full redraw
- **Ctrl-Q**: Quit
- **Esc**: Return to chat mode

## License

[0BSD](LICENSE) - Freedom to do whatever you want. 