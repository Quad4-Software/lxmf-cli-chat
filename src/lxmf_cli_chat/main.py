# SPDX-License-Identifier: 0BSD
# Copyright (c) 2026 Quad4

"""LXMF CLI Chat module for terminal-based communication over Reticulum."""

import os
import sys
import time
import threading
import signal
import logging
import RNS
import LXMF
from datetime import datetime
import select
import RNS.vendor.umsgpack as msgpack

# Constants for UI
COLOR_RESET = "\033[0m"
COLOR_HEADER = "\033[1;37;44m"
COLOR_SENDER = "\033[1;32m"
COLOR_TIMESTAMP = "\033[0;90m"
COLOR_STAMP = "\033[1;33m"
COLOR_ERROR = "\033[1;31m"
COLOR_INFO = "\033[1;36m"
COLOR_PN = "\033[1;35m"
COLOR_DIM = "\033[0;90m"

# Status icons
ICON_PENDING = "◷"
ICON_SENT = "✓"
ICON_DELIVERED = "✓✓"

logger = logging.getLogger("lxmf_cli_chat")

class PropagationAnnounceHandler:
    """Handler for LXMF propagation node announcements."""

    def __init__(self, callback):
        """Initialize the propagation announce handler."""
        self.aspect_filter = "lxmf.propagation"
        self.callback = callback

    def received_announce(self, destination_hash, announced_identity, app_data):
        """Process a received propagation announce."""
        self.callback(destination_hash, announced_identity, app_data)


class DeliveryAnnounceHandler:
    """Handler for LXMF delivery announcements."""

    def __init__(self, callback):
        """Initialize the delivery announce handler."""
        self.aspect_filter = "lxmf.delivery"
        self.callback = callback

    def received_announce(self, destination_hash, announced_identity, app_data):
        """Process a received delivery announce."""
        self.callback(destination_hash, announced_identity, app_data)


class LXMFChat:
    """Main class for the LXMF CLI Chat application."""

    def __init__(self, config_path=None, identity_path=None, display_name="Anonymous", debug=False, headless=False):
        """Initialize the LXMF chat application."""
        self.config_path = config_path
        self.storage_path = os.path.expanduser("~/.lxmf_cli_chat")
        self.headless = headless
        if not os.path.exists(self.storage_path):
            os.makedirs(self.storage_path, exist_ok=True)

        if debug:
            log_file = os.path.join(self.storage_path, "debug.log")
            logging.basicConfig(
                filename=log_file,
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )
            logger.info("Debug logging enabled")

        self.display_name = display_name
        try:
            self.r = RNS.Reticulum(configdir=self.config_path)
            self.router = LXMF.LXMRouter(storagepath=os.path.join(self.storage_path, "router"))
        except Exception as e:
            if not self.headless:
                logger.error(f"Failed to initialize Reticulum/LXMF: {e}")
            else:
                print(f"Error: Failed to initialize Reticulum/LXMF: {e}")
            raise

        if identity_path and os.path.exists(identity_path):
            self.identity = RNS.Identity.from_file(identity_path)
        else:
            local_identity_path = os.path.join(self.storage_path, "identity")
            if os.path.exists(local_identity_path):
                self.identity = RNS.Identity.from_file(local_identity_path)
            else:
                self.identity = RNS.Identity()
                self.identity.to_file(local_identity_path)

        self.source = self.router.register_delivery_identity(
            self.identity, 
            display_name=self.display_name,
            stamp_cost=8
        )
        self.router.register_delivery_callback(self.on_message)
        
        self.messages = {} # hash_hex -> list of messages
        self.active_sessions = [] # list of hash_hex, ordered by last activity
        self.pending_outbound = {}
        self.running = True
        self.lock = threading.RLock()
        
        self.input_buffer = ""
        self.cursor_pos = 0
        self.prompt = "> "
        self.status_msg = ""
        self.status_expiry = 0
        self.target_hash_hex = ""
        self.ui_mode = "chat"
        self.peer_list_scroll = 0
        self.selected_peer_idx = 0
        
        self.showing_manual = False
        
        self.history = []
        self.history_idx = -1
        self.saved_input = ""
        
        self.known_pns = {}
        self.auto_pn = True
        self.manual_pn = None
        self.active_pn = None
        self.last_pn_request = 0
        self.pn_request_interval = 1800

        self.send_mode = "auto" # auto, direct, propagated
        self.blocked_addresses = set()
        
        self.collected_announces = {}
        
        RNS.Transport.register_announce_handler(PropagationAnnounceHandler(self.on_pn_announce))
        RNS.Transport.register_announce_handler(DeliveryAnnounceHandler(self.on_delivery_announce))
        
        self.is_windows = os.name == 'nt'
        
        if not self.headless:
            self.bg_thread = threading.Thread(target=self.background_loop, daemon=True)

    def on_delivery_announce(self, dest_hash, ident, app_data):
        """Handle received delivery announcements."""
        hash_hex = RNS.hexrep(dest_hash, delimit=False)
        name = "Anonymous"
        try:
            decoded = msgpack.unpackb(app_data)
            if isinstance(decoded, list) and len(decoded) > 0:
                name = decoded[0].decode("utf-8") if decoded[0] else "Anonymous"
            elif isinstance(app_data, bytes):
                name = app_data.decode("utf-8")
        except:
            pass

        with self.lock:
            self.collected_announces[hash_hex] = {
                "name": name,
                "last_heard": time.time(),
                "hops": RNS.Transport.hops_to(dest_hash)
            }
            if hash_hex in self.pending_outbound:
                contents = self.pending_outbound.pop(hash_hex)
                if not self.headless:
                    self.set_status(f"{COLOR_INFO}Identity found for {name} ({hash_hex}), sending {len(contents)} pending messages...{COLOR_RESET}")
                else:
                    print(f"Identity found for {name} ({hash_hex}), sending {len(contents)} pending messages...")
                for content in contents:
                    self.send_message(hash_hex, content)

    def on_pn_announce(self, dest_hash, ident, app_data):
        """Handle received propagation node announcements."""
        hops = RNS.Transport.hops_to(dest_hash)
        name = "Anonymous PN"
        try:
            data = msgpack.unpackb(app_data)
            if len(data) > 6 and isinstance(data[6], dict):
                if 0x01 in data[6]:
                    name = data[6][0x01].decode("utf-8")
        except:
            pass
            
        with self.lock:
            self.known_pns[dest_hash] = {
                "hops": hops,
                "last_heard": time.time(),
                "name": name,
                "ident": ident
            }
        self.update_active_pn()

    def update_active_pn(self):
        """Update the currently active propagation node."""
        with self.lock:
            if not self.auto_pn and self.manual_pn:
                new_pn = self.manual_pn
            elif self.auto_pn and self.known_pns:
                # Get best hops available
                best_hops = min(d["hops"] for d in self.known_pns.values())
                # If current PN is still valid and has best hops, keep it to avoid rapid switching
                if self.active_pn and self.active_pn in self.known_pns and self.known_pns[self.active_pn]["hops"] <= best_hops:
                    new_pn = self.active_pn
                else:
                    # Otherwise pick the newest one with best hops
                    best_pns = [h for h, d in self.known_pns.items() if d["hops"] == best_hops]
                    new_pn = sorted(best_pns, key=lambda h: self.known_pns[h]["last_heard"], reverse=True)[0]
            else:
                new_pn = None
            
            if new_pn != self.active_pn:
                self.active_pn = new_pn
                if self.active_pn:
                    self.router.set_outbound_propagation_node(self.active_pn)
                    if not self.headless:
                        self.set_status(f"{COLOR_PN}Active PN: {RNS.prettyhexrep(self.active_pn)}{COLOR_RESET}")
                else:
                    self.router.set_outbound_propagation_node(None)
                    if not self.headless:
                        self.set_status(f"{COLOR_PN}No active PN{COLOR_RESET}")

    def on_message(self, lxm):
        """Handle received LXMF messages."""
        try:
            with self.lock:
                sender_hash_hex = RNS.hexrep(lxm.source_hash, delimit=False)
                
                if sender_hash_hex in self.blocked_addresses:
                    logger.debug(f"Ignored message from blocked address {sender_hash_hex}")
                    return

                sender_name = "Anonymous"
                if sender_hash_hex in self.collected_announces:
                    sender_name = self.collected_announces[sender_hash_hex]["name"]
                else:
                    # Sync sender info to collected announces
                    self.collected_announces[sender_hash_hex] = {
                        "name": "Anonymous",
                        "last_heard": time.time(),
                        "hops": RNS.Transport.hops_to(lxm.source_hash)
                    }
                
                msg_time = datetime.fromtimestamp(lxm.timestamp).strftime("%H:%M:%S") if lxm.timestamp else datetime.now().strftime("%H:%M:%S")
                
                hops = RNS.Transport.hops_to(lxm.source_hash)
                
                msg_data = {
                    "time": msg_time,
                    "sender_hash": RNS.prettyhexrep(lxm.source_hash),
                    "sender_name": sender_name,
                    "content": str(lxm.content_as_string() or ""),
                    "stamped": lxm.stamp_valid,
                    "signed": lxm.signature_validated,
                    "hops": hops
                }

                if sender_hash_hex not in self.messages:
                    self.messages[sender_hash_hex] = []
                
                self.messages[sender_hash_hex].append(msg_data)
                if len(self.messages[sender_hash_hex]) > 500:
                    self.messages[sender_hash_hex].pop(0)

                # Update active sessions
                if sender_hash_hex in self.active_sessions:
                    self.active_sessions.remove(sender_hash_hex)
                self.active_sessions.insert(0, sender_hash_hex)

                if not self.target_hash_hex and not self.headless:
                    # Auto-focus if no target set
                    self.target_hash_hex = sender_hash_hex
                    self.set_status(f"{COLOR_INFO}New chat from {sender_name}{COLOR_RESET}")
            
            if not self.headless:
                self.refresh_ui()
            else:
                print(f"[{msg_time}] {sender_name}: {lxm.content_as_string()}")
        except Exception as e:
            if not self.headless:
                logger.error(f"Error in on_message: {e}")
                self.set_status(f"{COLOR_ERROR}Error receiving message: {e}{COLOR_RESET}")
            else:
                print(f"Error receiving message: {e}")

    def get_term_size(self):
        """Get the current terminal size."""
        try:
            return os.get_terminal_size()
        except OSError:
            return type('size', (), {'columns': 80, 'lines': 24})

    def show_manual(self):
        """Display a help manual."""
        self.showing_manual = True
        width = self.get_term_size().columns
        height = self.get_term_size().lines
        
        manual = [
            " LXMF CLI CHAT MANUAL ",
            "======================",
            "",
            "CORE COMMANDS:",
            "  /t, /target <hex>    Set destination by full or partial hash",
            "  /c, /chat [idx]      Switch to an active session (or cycle)",
            "  /r, /reply [msg]     Reply to the most recent conversation",
            "  /p, /peers           Browse discovered peers list",
            "  /s, /search <query>  Search peers by name or hash",
            "  /n, /name <name>     Change your display name",
            "  /a, /announce        Send an announce to the network",
            "  /id                  Show your own destination hash",
            "  /q, /quit            Exit the application",
            "",
            "ADVANCED COMMANDS:",
            "  /mode <m>            Set send mode: direct, propagated, or auto",
            "  /pn <cmd>            Propagation Node: auto, list, fetch, or <hex>",
            "  /stamp <cost>        Set your inbound message stamp cost (1-255)",
            "  /grant [hex]         Send a message and grant a one-time ticket",
            "  /block [hex]         Block a destination hash",
            "  /unblock <hex>       Unblock a destination hash",
            "  /blocked             List all blocked addresses",
            "",
            "TUI SHORTCUTS:",
            "  Ctrl-P               Browse peers",
            "  Ctrl-N               Cycle active chats",
            "  Ctrl-A               Send announce",
            "  Ctrl-L               Redraw screen",
            "  Ctrl-Q               Quit",
            "  Esc                  Return to chat mode",
            "",
            "Press any key to return to chat..."
        ]
        
        buf = ["\033[2J\033[H"]
        for i, line in enumerate(manual):
            if i + 3 < height:
                buf.append(f"\033[{i+3};5H{line}")
        
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
        
        # Wait for any key
        if self.is_windows:
            import msvcrt
            msvcrt.getch()
        else:
            import tty, termios
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        
        self.showing_manual = False
        self.refresh_ui()

    def refresh_ui(self):
        """Refresh the TUI."""
        if self.headless or self.showing_manual:
            return
        size = self.get_term_size()
        width = size.columns
        height = size.lines
        
        # Use a buffer to reduce flicker
        buf = []
        
        # Clear screen and move to top
        buf.append("\033[2J\033[H")
        
        # Header (Lines 1 & 2)
        header_text = f" LXMF CLI Chat | {self.display_name} | {RNS.prettyhexrep(self.source.hash)} "
        if self.target_hash_hex:
            name = self.collected_announces.get(self.target_hash_hex, {}).get("name", "Anonymous")
            header_text += f" | Chatting with: {name} ({self.target_hash_hex}) "
        
        pn_status = "Auto" if self.auto_pn else "Manual"
        pn_id = RNS.prettyhexrep(self.active_pn) if self.active_pn else "None"
        mode_str = self.send_mode.upper()
        header_pn = f" PN [{pn_status}]: {pn_id} | Mode: {self.ui_mode.upper()} | Send: {mode_str} "
        
        buf.append(f"{COLOR_HEADER}{header_text.center(width)}{COLOR_RESET}\n")
        buf.append(f"{COLOR_HEADER}{header_pn.center(width)}{COLOR_RESET}")
        
        # Decide if we use side-panel (if width is large enough)
        use_side_panel = width > 100 and self.ui_mode == "chat"
        chat_width = width - 35 if use_side_panel else width
        
        if self.ui_mode == "chat":
            # Messages area (starts at line 3)
            msg_area_height = height - 4 # Lines 3 to height-2
            wrapped_lines = []
            with self.lock:
                msgs = self.messages.get(self.target_hash_hex, []) if self.target_hash_hex else []
                for msg in msgs:
                    msg_time = str(msg.get('time', '??:??:??'))
                    sender = str(msg.get('sender_name', 'Anonymous'))
                    time_str = f"{COLOR_TIMESTAMP}[{msg_time}]{COLOR_RESET}"
                    name_str = f"{COLOR_SENDER}{sender}{COLOR_RESET}"
                    
                    # Metadata
                    meta = []
                    hops = msg.get("hops")
                    if hops is not None:
                        meta.append(f"{hops}h")
                    if msg.get("signed"):
                        meta.append("S")
                    if msg.get("stamped"):
                        meta.append("ST")
                    
                    meta_str = f"{COLOR_DIM}({','.join(meta)}){COLOR_RESET} " if meta else ""

                    # Status icon
                    status_icon = str(msg.get("status_icon", ICON_SENT))
                    if sender == "Me":
                        method = msg.get('method', 'Direct')
                        method_char = str(method)[0] if method else "?"
                        status_str = f" \033[0;34m({method_char})\033[0m {status_icon}"
                        status_msg = msg.get("status")
                        if status_msg and "retrying" in str(status_msg):
                            status_str += f" {COLOR_DIM}{status_msg}{COLOR_RESET}"
                    else:
                        status_str = f" {status_icon}"

                    prefix = f"[{msg_time}] {sender}: "
                    content = str(msg.get('content', ''))
                    
                    # Wrap logic
                    max_content_w = max(10, chat_width - len(prefix) - 25)
                    chunks = [content[i:i+max_content_w] for i in range(0, len(content), max_content_w)]
                    
                    for i, chunk in enumerate(chunks):
                        if i == 0:
                            wrapped_lines.append(f"{time_str} {name_str}: {meta_str}{chunk}{status_str}")
                        else:
                            indent = " " * (len(msg_time) + 3)
                            wrapped_lines.append(f"{indent}{COLOR_DIM}{chunk}{COLOR_RESET}")

            # Display messages from line 3 downwards
            display_msgs = wrapped_lines[-(msg_area_height):]
            if not self.target_hash_hex and not display_msgs:
                buf.append(f"\033[3;1H{COLOR_INFO} Welcome to LXMF CLI Chat!{COLOR_RESET}")
                buf.append(f"\033[4;1H No active chat selected.")
                buf.append(f"\033[5;1H Use /t <hex> to start a chat or /p to browse peers.")
            
            for i in range(msg_area_height):
                current_line = i + 3
                buf.append(f"\033[{current_line};1H\033[K") # Move and clear line
                
                if i < len(display_msgs):
                    buf.append(display_msgs[i])
                
                if use_side_panel:
                    # Side panel content: Active Chats
                    with self.lock:
                        sessions = self.active_sessions
                    
                    buf.append(f"\033[{current_line};{chat_width+2}H") # Move to side panel start
                    if i == 0:
                        buf.append(f"\033[1;37;42m ACTIVE CHATS \033[0m")
                    elif 1 <= i <= len(sessions):
                        h = sessions[i-1]
                        name = str(self.collected_announces.get(h, {}).get("name", "Anonymous"))
                        marker = "*" if h == self.target_hash_hex else " "
                        session_info = f"{marker} {name[:12]} ({str(h)[:6]})"
                        if h == self.target_hash_hex:
                            buf.append(f"\033[1;32m{session_info}\033[0m")
                        else:
                            buf.append(session_info)
                    elif i == len(sessions) + 2:
                        buf.append(f"\033[1;37;45m DISCOVERED \033[0m")
                    elif len(sessions) + 3 <= i <= len(sessions) + 3 + 10:
                        # Show some discovered peers as well
                        with self.lock:
                            all_peers = [p for p in sorted(self.collected_announces.items(), key=lambda x: x[1]['last_heard'], reverse=True) if p[0] not in sessions]
                        
                        peer_idx = i - (len(sessions) + 3)
                        if peer_idx < len(all_peers):
                            h, d = all_peers[peer_idx]
                            name = str(d.get("name", "Anonymous"))
                            peer_info = f"  {name[:12]} ({str(h)[:6]})"
                            buf.append(peer_info)
                    
        elif self.ui_mode == "peers":
            # Peer list area (Full Screen, starts at line 3)
            list_area_height = height - 4
            with self.lock:
                all_peers = sorted(self.collected_announces.items(), key=lambda x: x[1]['last_heard'], reverse=True)
                if not all_peers:
                    buf.append(f"\033[3;1H No peers discovered yet.")
                else:
                    visible_peers = all_peers[self.peer_list_scroll : self.peer_list_scroll + list_area_height]
                    for i, (h, d) in enumerate(visible_peers):
                        idx = i + self.peer_list_scroll
                        marker = "> " if idx == self.selected_peer_idx else "  "
                        color = "\033[1;37;42m" if idx == self.selected_peer_idx else ""
                        reset = "\033[0m" if idx == self.selected_peer_idx else ""
                        last_heard_val = d.get('last_heard', 0)
                        last_heard = datetime.fromtimestamp(last_heard_val).strftime("%H:%M:%S")
                        name = str(d.get('name', 'Anonymous'))
                        hops = str(d.get('hops', '?'))
                        line = f"{marker}{color}{name[:20].ljust(20)} ({str(h)}) | Hops: {hops} | Seen: {last_heard}{reset}"
                        buf.append(f"\033[{i+3};1H\033[K{line}")
            buf.append(f"\033[{height-1};1H\033[K (Up/Down to scroll, Enter to select, Esc to back)")
        
        # Status line
        buf.append(f"\033[{height-1};1H\033[K") 
        if self.status_msg:
            buf.append(f"{self.status_msg[:width]}")
        
        # Prompt line
        buf.append(f"\033[{height};1H\033[K{self.prompt}{self.input_buffer}")
        # Position cursor
        buf.append(f"\033[{height};{len(self.prompt) + self.cursor_pos + 1}H")
        
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def set_status(self, msg, duration=5):
        """Set the status message to be displayed in the UI."""
        msg = str(msg)
        if self.headless:
            print(f"Status: {msg}")
            return
        with self.lock:
            self.status_msg = msg
            self.status_expiry = time.time() + duration

    def handle_command(self, cmd_str):
        """Process and execute a user command."""
        if not self.headless:
            # Save to history
            if not self.history or self.history[-1] != cmd_str:
                self.history.append(cmd_str)
                if len(self.history) > 100:
                    self.history.pop(0)
            self.history_idx = -1
        
        # Split command and args
        parts = cmd_str.split(" ", 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("/target", "/t", "/hash"):
            try:
                target_input = args.strip().lower()
                resolved_hash = None
                peer_name = "Anonymous"
                
                # Try to resolve from collected announces
                matches = []
                with self.lock:
                    for h, d in self.collected_announces.items():
                        if h.lower().startswith(target_input):
                            matches.append((h, d))
                
                if len(matches) == 1:
                    resolved_hash, peer_data = matches[0]
                    peer_name = peer_data['name']
                elif len(matches) > 1:
                    match_list = ", ".join([f"{d['name']}({h[:6]})" for h, d in matches[:3]])
                    self.set_status(f"{COLOR_ERROR}Ambiguous: {match_list}...{COLOR_RESET}")
                    return
                elif len(target_input) == 32:
                    # Direct full hash
                    resolved_hash = target_input
                else:
                    self.set_status(f"{COLOR_ERROR}Could not resolve hash: {target_input}{COLOR_RESET}")
                    return

                if resolved_hash:
                    self.target_hash_hex = resolved_hash
                    self.ui_mode = "chat"
                    self.set_status(f"{COLOR_INFO}Target: {peer_name} | {resolved_hash}{COLOR_RESET}")
            except:
                self.set_status(f"{COLOR_ERROR}Usage: /t <hash|short_hash>{COLOR_RESET}")
        elif cmd in ("/search", "/s"):
            query = args.strip().lower()
            with self.lock:
                results = [f"{v['name']} ({k})" for k, v in self.collected_announces.items() if query in v['name'].lower() or query in k.lower()]
                if results:
                    self.set_status(f"{COLOR_INFO}Found: {', '.join(results[:3])}...{COLOR_RESET}", duration=10)
                else:
                    self.set_status(f"{COLOR_ERROR}No matches for '{query}'{COLOR_RESET}")
        elif cmd in ("/peers", "/p"):
            with self.lock:
                if not self.collected_announces:
                    self.set_status(f"{COLOR_INFO}No peers discovered yet.{COLOR_RESET}")
                else:
                    self.ui_mode = "peers"
                    self.selected_peer_idx = 0
                    self.peer_list_scroll = 0
                    self.set_status(f"{COLOR_INFO}Switched to Peer List mode{COLOR_RESET}")
        elif cmd in ("/chat", "/c"):
            try:
                idx = int(args.strip()) - 1
                with self.lock:
                    if 0 <= idx < len(self.active_sessions):
                        self.target_hash_hex = self.active_sessions[idx]
                        self.ui_mode = "chat"
                        name = self.collected_announces.get(self.target_hash_hex, {}).get("name", "Anonymous")
                        self.set_status(f"{COLOR_INFO}Switched to chat with {name}{COLOR_RESET}")
                    else:
                        self.set_status(f"{COLOR_ERROR}Invalid session index{COLOR_RESET}")
            except:
                with self.lock:
                    if self.active_sessions:
                        self.ui_mode = "chat"
                        # Cycle sessions
                        if self.target_hash_hex in self.active_sessions:
                            curr_idx = self.active_sessions.index(self.target_hash_hex)
                            next_idx = (curr_idx + 1) % len(self.active_sessions)
                            self.target_hash_hex = self.active_sessions[next_idx]
                        else:
                            self.target_hash_hex = self.active_sessions[0]
                        name = self.collected_announces.get(self.target_hash_hex, {}).get("name", "Anonymous")
                        self.set_status(f"{COLOR_INFO}Switched to chat with {name}{COLOR_RESET}")
                    else:
                        self.set_status(f"{COLOR_ERROR}No active sessions{COLOR_RESET}")
        elif cmd in ("/reply", "/r"):
            with self.lock:
                if self.active_sessions:
                    self.target_hash_hex = str(self.active_sessions[0])
                    self.ui_mode = "chat"
                    name = str(self.collected_announces.get(self.target_hash_hex, {}).get("name", "Anonymous"))
                    if args:
                        self.send_message(self.target_hash_hex, str(args))
                    else:
                        self.set_status(f"{COLOR_INFO}Replying to {name}{COLOR_RESET}")
                else:
                    self.set_status(f"{COLOR_ERROR}No one to reply to{COLOR_RESET}")
        elif cmd == "/mode":
            mode = args.strip().lower()
            if mode in ("direct", "propagated", "auto"):
                self.send_mode = mode
                self.set_status(f"{COLOR_INFO}Send mode set to {mode.upper()}{COLOR_RESET}")
            else:
                self.set_status(f"{COLOR_ERROR}Usage: /mode <direct|propagated|auto>{COLOR_RESET}")
        elif cmd in ("/grant", "/ticket"):
            target = args.strip().lower()
            if not target and self.target_hash_hex:
                target = self.target_hash_hex
            
            if len(target) == 32:
                self.send_message(target, "Ticket granted", include_ticket=True)
                self.set_status(f"{COLOR_INFO}Granting ticket to {target}{COLOR_RESET}")
            else:
                self.set_status(f"{COLOR_ERROR}Usage: /grant <hex_hash>{COLOR_RESET}")
        elif cmd == "/stamp":
            try:
                cost = int(args.strip())
                if 1 <= cost <= 255:
                    self.router.set_inbound_stamp_cost(self.source.hash, cost)
                    self.set_status(f"{COLOR_INFO}Inbound stamp cost set to {cost}{COLOR_RESET}")
                else:
                    self.set_status(f"{COLOR_ERROR}Stamp cost must be 1-255{COLOR_RESET}")
            except:
                self.set_status(f"{COLOR_ERROR}Usage: /stamp <cost>{COLOR_RESET}")
        elif cmd == "/block":
            target = args.strip().lower()
            if not target and self.target_hash_hex:
                target = self.target_hash_hex
            
            if len(target) == 32:
                with self.lock:
                    self.blocked_addresses.add(target)
                self.set_status(f"{COLOR_INFO}Blocked {target}{COLOR_RESET}")
            else:
                self.set_status(f"{COLOR_ERROR}Usage: /block <hex_hash>{COLOR_RESET}")
        elif cmd == "/unblock":
            target = args.strip().lower()
            if len(target) == 32:
                with self.lock:
                    if target in self.blocked_addresses:
                        self.blocked_addresses.remove(target)
                        self.set_status(f"{COLOR_INFO}Unblocked {target}{COLOR_RESET}")
                    else:
                        self.set_status(f"{COLOR_ERROR}{target} is not blocked{COLOR_RESET}")
            else:
                self.set_status(f"{COLOR_ERROR}Usage: /unblock <hex_hash>{COLOR_RESET}")
        elif cmd == "/blocked":
            with self.lock:
                if not self.blocked_addresses:
                    self.set_status(f"{COLOR_INFO}No blocked addresses{COLOR_RESET}")
                else:
                    self.set_status(f"{COLOR_INFO}Blocked: {', '.join(list(self.blocked_addresses))}{COLOR_RESET}", duration=10)
        elif cmd in ("/name", "/n"):
            new_name = args.strip()
            if new_name:
                self.display_name = new_name
                # Re-register with new name
                self.source = self.router.register_delivery_identity(self.identity, display_name=self.display_name)
                self.set_status(f"{COLOR_INFO}Name changed to {self.display_name}{COLOR_RESET}")
        elif cmd in ("/announce", "/a"):
            self.router.announce(self.source.hash)
            self.set_status(f"{COLOR_INFO}Announce sent{COLOR_RESET}")
        elif cmd == "/id":
            self.set_status(f"{COLOR_INFO}Your ID: {RNS.prettyhexrep(self.source.hash)}{COLOR_RESET}")
        elif cmd == "/pn":
            parts = args.split(" ")
            subcmd = parts[0].lower()
            if subcmd == "auto":
                self.auto_pn = True
                self.manual_pn = None
                self.update_active_pn()
                self.set_status(f"{COLOR_PN}Propagation Node auto-config enabled{COLOR_RESET}")
            elif subcmd == "list":
                with self.lock:
                    if not self.known_pns:
                        self.set_status(f"{COLOR_PN}No propagation nodes known{COLOR_RESET}")
                    else:
                        pn_list = ", ".join([f"{RNS.prettyhexrep(h)} ({d['hops']}h)" for h, d in self.known_pns.items()])
                        self.set_status(f"{COLOR_PN}Known PNs: {pn_list}{COLOR_RESET}")
            elif subcmd == "fetch":
                if self.active_pn:
                    self.router.request_messages_from_propagation_node(self.identity)
                    self.last_pn_request = time.time()
                    self.set_status(f"{COLOR_PN}Requested messages from PN{COLOR_RESET}")
                else:
                    self.set_status(f"{COLOR_ERROR}No active PN to fetch from{COLOR_RESET}")
            else:
                try:
                    self.manual_pn = bytes.fromhex(subcmd)
                    self.auto_pn = False
                    self.update_active_pn()
                    self.set_status(f"{COLOR_PN}Manual PN set to {subcmd}{COLOR_RESET}")
                except:
                    self.set_status(f"{COLOR_ERROR}Usage: /pn <hex|auto|list|fetch>{COLOR_RESET}")
        elif cmd in ("/help", "/h", "/manual"):
            self.show_manual()
            self.set_status(f"{COLOR_INFO}Displaying manual. Press any key or Esc to return.{COLOR_RESET}")
        elif cmd in ("/quit", "/q"):
            self.running = False
        else:
            if self.target_hash_hex:
                self.send_message(self.target_hash_hex, str(cmd_str))
            else:
                self.set_status(f"{COLOR_ERROR}No target set. Use /t <hex> or /p to browse peers.{COLOR_RESET}")

    def send_message(self, destination_hash_hex, content, include_ticket=False):
        """Send an LXMF message to a destination."""
        try:
            dest_hash = bytes.fromhex(destination_hash_hex)
            
            # Try to recall identity
            recipient_identity = RNS.Identity.recall(dest_hash)
            
            # If identity unknown, request path and queue message
            if not recipient_identity:
                if not RNS.Transport.has_path(dest_hash):
                    self.set_status(f"{COLOR_INFO}Requesting path to {destination_hash_hex}...{COLOR_RESET}")
                    RNS.Transport.request_path(dest_hash)
                else:
                    self.set_status(f"{COLOR_INFO}Path known, but identity unknown. Requesting...{COLOR_RESET}")
                    RNS.Transport.request_path(dest_hash) # Requesting path again often triggers an announce
                
                with self.lock:
                    if destination_hash_hex not in self.pending_outbound:
                        self.pending_outbound[destination_hash_hex] = []
                    self.pending_outbound[destination_hash_hex].append(content)
                return

            dest = RNS.Destination(recipient_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")

            # Determine send method
            if self.send_mode == "direct":
                method = LXMF.LXMessage.DIRECT
            elif self.send_mode == "propagated":
                method = LXMF.LXMessage.PROPAGATED
            else: # auto
                method = LXMF.LXMessage.DIRECT
                if not RNS.Transport.has_path(dest_hash) and self.active_pn:
                    method = LXMF.LXMessage.PROPAGATED
            
            lxm = LXMF.LXMessage(dest, self.source, str(content or ""), desired_method=method, include_ticket=include_ticket)
            lxm.pack()
            
            # Track delivery status
            msg_id = RNS.hexrep(lxm.hash, delimit=False)
            
            def delivery_callback(message):
                with self.lock:
                    # Find message in history and update status
                    for h_hex, session_msgs in self.messages.items():
                        for m in session_msgs:
                            if m.get("msg_id") == msg_id:
                                m["status"] = "delivered"
                                m["status_icon"] = ICON_DELIVERED
                                break
                if not self.headless:
                    self.refresh_ui()

            def failed_callback(message):
                with self.lock:
                    for h_hex, session_msgs in self.messages.items():
                        for m in session_msgs:
                            if m.get("msg_id") == msg_id:
                                retries = m.get("retries", 0)
                                if retries < 3:
                                    m["retries"] = retries + 1
                                    m["status"] = f"retrying ({m['retries']}/3)"
                                    # Trigger retry through router
                                    self.router.handle_outbound(message)
                                else:
                                    m["status"] = "failed"
                                    m["status_icon"] = "!"
                                break
                if not self.headless:
                    self.refresh_ui()

            lxm.register_delivery_callback(delivery_callback)
            lxm.register_failed_callback(failed_callback)
            
            self.router.handle_outbound(lxm)
            
            with self.lock:
                msg_data = {
                    "msg_id": msg_id,
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "sender_name": "Me",
                    "sender_hash": RNS.prettyhexrep(self.source.hash),
                    "content": str(content or ""),
                    "stamped": True,
                    "method": "Direct" if method == LXMF.LXMessage.DIRECT else "Propagated",
                    "status": "sent",
                    "status_icon": ICON_SENT,
                    "retries": 0
                }
                
                if destination_hash_hex not in self.messages:
                    self.messages[destination_hash_hex] = []
                
                self.messages[destination_hash_hex].append(msg_data)
                
                # Update active sessions
                if destination_hash_hex in self.active_sessions:
                    self.active_sessions.remove(destination_hash_hex)
                self.active_sessions.insert(0, destination_hash_hex)
            
            if not self.headless:
                self.refresh_ui()
            
            if self.headless:
                print(f"Message dispatched to {destination_hash_hex} via {msg_data.get('method', 'Unknown')}")
        except Exception as e:
            self.set_status(f"{COLOR_ERROR}Error: {str(e)}{COLOR_RESET}")

    def background_loop(self):
        """Run the background maintenance loop."""
        while self.running:
            # Check if we should request messages from PN
            if self.active_pn and (time.time() - self.last_pn_request > self.pn_request_interval):
                self.router.request_messages_from_propagation_node(self.identity)
                self.last_pn_request = time.time()
            
            # Check if status has expired
            with self.lock:
                if self.status_msg and time.time() > self.status_expiry:
                    self.status_msg = ""
            
            # Periodically refresh UI to show status changes or new messages
            # without waiting for input
            time.sleep(0.5)
            self.refresh_ui()

    def run(self):
        """Start the application and its main loop."""
        self.router.announce(self.source.hash)
        
        # Setup SIGWINCH for immediate resize refresh (not on Windows)
        if not self.is_windows:
            def handle_resize(sig, frame):
                self.refresh_ui()
            signal.signal(signal.SIGWINCH, handle_resize)
            
        self.bg_thread.start()
        
        if self.is_windows:
            self.run_windows()
        else:
            self.run_unix()

    def run_unix(self):
        """Run the main loop for Unix-like systems."""
        import tty, termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while self.running:
                # Use select to avoid blocking indefinitely, allowing background updates
                rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
                if rlist:
                    char = sys.stdin.read(1)
                    if char == "\x03": # Ctrl-C
                        break
                    elif char == "\x10": # Ctrl-P
                        self.handle_command("/p")
                    elif char == "\x0e": # Ctrl-N
                        self.handle_command("/c")
                    elif char == "\x01": # Ctrl-A
                        self.handle_command("/a")
                    elif char == "\x0c": # Ctrl-L
                        self.refresh_ui()
                    elif char == "\x11": # Ctrl-Q
                        self.running = False
                    elif char in ("\r", "\n"):
                        if self.ui_mode == "peers":
                            # Select peer
                            with self.lock:
                                all_peers = sorted(self.collected_announces.items(), key=lambda x: x[1]['last_heard'], reverse=True)
                                if all_peers and self.selected_peer_idx < len(all_peers):
                                    h, d = all_peers[self.selected_peer_idx]
                                    self.target_hash_hex = h
                                    self.ui_mode = "chat"
                                    self.set_status(f"{COLOR_INFO}Chatting with {d['name']} ({h}){COLOR_RESET}")
                        else:
                            if self.input_buffer:
                                self.handle_command(self.input_buffer)
                            self.input_buffer = ""
                            self.cursor_pos = 0
                    elif char == "\x7f": # Backspace
                        if self.cursor_pos > 0:
                            self.input_buffer = self.input_buffer[:self.cursor_pos-1] + self.input_buffer[self.cursor_pos:]
                            self.cursor_pos -= 1
                    elif char == "\x1b": # Escape sequences (arrows, etc)
                        # Use a small timeout to see if more chars are coming
                        r, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if r:
                            seq = sys.stdin.read(2)
                            if seq == "[A": # Up
                                if self.ui_mode == "peers":
                                    self.selected_peer_idx = max(0, self.selected_peer_idx - 1)
                                    if self.selected_peer_idx < self.peer_list_scroll:
                                        self.peer_list_scroll = self.selected_peer_idx
                                else:
                                    # History up
                                    if self.history:
                                        if self.history_idx == -1:
                                            self.saved_input = self.input_buffer
                                        self.history_idx = min(len(self.history) - 1, self.history_idx + 1)
                                        self.input_buffer = self.history[-(self.history_idx + 1)]
                                        self.cursor_pos = len(self.input_buffer)
                            elif seq == "[B": # Down
                                if self.ui_mode == "peers":
                                    with self.lock:
                                        num_peers = len(self.collected_announces)
                                    if num_peers > 0:
                                        self.selected_peer_idx = min(num_peers - 1, self.selected_peer_idx + 1)
                                        area_h = self.get_term_size().lines - 5
                                        if self.selected_peer_idx >= self.peer_list_scroll + area_h:
                                            self.peer_list_scroll = self.selected_peer_idx - area_h + 1
                                else:
                                    # History down
                                    if self.history_idx > 0:
                                        self.history_idx -= 1
                                        self.input_buffer = self.history[-(self.history_idx + 1)]
                                        self.cursor_pos = len(self.input_buffer)
                                    elif self.history_idx == 0:
                                        self.history_idx = -1
                                        self.input_buffer = self.saved_input
                                        self.cursor_pos = len(self.input_buffer)
                            elif seq == "[D" and self.cursor_pos > 0: # Left
                                self.cursor_pos -= 1
                            elif seq == "[C" and self.cursor_pos < len(self.input_buffer): # Right
                                self.cursor_pos += 1
                        else: # Just Esc
                            self.ui_mode = "chat"
                    elif char == "\x15": # Ctrl-U: Clear line
                        self.input_buffer = ""
                        self.cursor_pos = 0
                    elif char == "\x17": # Ctrl-W: Delete word
                        before = self.input_buffer[:self.cursor_pos].rstrip()
                        sep = before.rfind(" ")
                        if sep == -1:
                            self.input_buffer = self.input_buffer[self.cursor_pos:]
                            self.cursor_pos = 0
                        else:
                            self.input_buffer = before[:sep+1] + self.input_buffer[self.cursor_pos:]
                            self.cursor_pos = sep + 1
                    else:
                        # Insert character
                        self.input_buffer = self.input_buffer[:self.cursor_pos] + char + self.input_buffer[self.cursor_pos:]
                        self.cursor_pos += 1
                    self.refresh_ui()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def run_windows(self):
        """Run the main loop for Windows systems."""
        import msvcrt
        while self.running:
            if msvcrt.kbhit():
                char = msvcrt.getch()
                if char == b"\x03": # Ctrl-C
                    break
                elif char == b"\x10": # Ctrl-P
                    self.handle_command("/p")
                elif char == b"\x0e": # Ctrl-N
                    self.handle_command("/c")
                elif char == b"\x01": # Ctrl-A
                    self.handle_command("/a")
                elif char == b"\x0c": # Ctrl-L
                    self.refresh_ui()
                elif char == b"\x11": # Ctrl-Q
                    self.running = False
                elif char in (b"\r", b"\n"):
                    if self.ui_mode == "peers":
                        with self.lock:
                            all_peers = sorted(self.collected_announces.items(), key=lambda x: x[1]['last_heard'], reverse=True)
                            if all_peers and self.selected_peer_idx < len(all_peers):
                                h, d = all_peers[self.selected_peer_idx]
                                self.target_hash_hex = h
                                self.ui_mode = "chat"
                                self.set_status(f"{COLOR_INFO}Chatting with {d['name']} ({h}){COLOR_RESET}")
                    else:
                        if self.input_buffer:
                            self.handle_command(self.input_buffer)
                        self.input_buffer = ""
                        self.cursor_pos = 0
                elif char == b"\x08": # Backspace
                    if self.cursor_pos > 0:
                        self.input_buffer = self.input_buffer[:self.cursor_pos-1] + self.input_buffer[self.cursor_pos:]
                        self.cursor_pos -= 1
                elif char == b"\x1b": # Esc
                    self.ui_mode = "chat"
                elif char == b"\xe0": # Special keys
                    spec = msvcrt.getch()
                    if spec == b"H": # Up
                        if self.ui_mode == "peers":
                            self.selected_peer_idx = max(0, self.selected_peer_idx - 1)
                            if self.selected_peer_idx < self.peer_list_scroll:
                                self.peer_list_scroll = self.selected_peer_idx
                        else:
                            # History up
                            if self.history:
                                if self.history_idx == -1:
                                    self.saved_input = self.input_buffer
                                self.history_idx = min(len(self.history) - 1, self.history_idx + 1)
                                self.input_buffer = self.history[-(self.history_idx + 1)]
                                self.cursor_pos = len(self.input_buffer)
                    elif spec == b"P": # Down
                        if self.ui_mode == "peers":
                            with self.lock:
                                num_peers = len(self.collected_announces)
                            if num_peers > 0:
                                self.selected_peer_idx = min(num_peers - 1, self.selected_peer_idx + 1)
                                area_h = self.get_term_size().lines - 5
                                if self.selected_peer_idx >= self.peer_list_scroll + area_h:
                                    self.peer_list_scroll = self.selected_peer_idx - area_h + 1
                        else:
                            # History down
                            if self.history_idx > 0:
                                self.history_idx -= 1
                                self.input_buffer = self.history[-(self.history_idx + 1)]
                                self.cursor_pos = len(self.input_buffer)
                            elif self.history_idx == 0:
                                self.history_idx = -1
                                self.input_buffer = self.saved_input
                                self.cursor_pos = len(self.input_buffer)
                    elif spec == b"K" and self.cursor_pos > 0: # Left
                        self.cursor_pos -= 0
                    elif spec == b"M" and self.cursor_pos < len(self.input_buffer): # Right
                        self.cursor_pos += 1
                else:
                    try:
                        c = char.decode("utf-8")
                        self.input_buffer = self.input_buffer[:self.cursor_pos] + c + self.input_buffer[self.cursor_pos:]
                        self.cursor_pos += 1
                    except:
                        pass
                self.refresh_ui()
            time.sleep(0.01)

def main():
    """Entry point for the LXMF CLI Chat application."""
    import argparse
    parser = argparse.ArgumentParser(description="LXMF CLI Chat")
    parser.add_argument("--name", default="Anonymous", help="Display name")
    parser.add_argument("--config", help="Custom Reticulum config directory")
    parser.add_argument("--identity", help="Custom Reticulum identity file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to file")
    parser.add_argument("--send-to", help="Recipient hash (for non-interactive sending)")
    parser.add_argument("--msg", help="Message content (for non-interactive sending)")
    args = parser.parse_args()
    
    if args.send_to and args.msg:
        # Headless send
        chat = LXMFChat(config_path=args.config, identity_path=args.identity, display_name=args.name, debug=args.debug, headless=True)
        chat.send_message(args.send_to, args.msg)
        # Give Reticulum a moment to dispatch
        time.sleep(2)
    else:
        # Full TUI
        chat = LXMFChat(config_path=args.config, identity_path=args.identity, display_name=args.name, debug=args.debug)
        chat.run()

if __name__ == "__main__":
    main()
