"""
World — Ядро локального P2P-обмена (Strict RFC1918 LAN Only)
Работает исключительно в локальной сети (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16).
Любые внешние (WAN / Интернет) IP-адреса блокируются на уровне сокетов.
"""

import hashlib
import ipaddress
import json
import os
import platform
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

DISCOVERY_PORT = 45454
TRANSFER_PORT = 45455
BUFFER_SIZE = 65536
MAGIC_HEADER = b"WORLD_LAN_V1"


def is_private_lan_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            return False
        return ip.is_private
    except ValueError:
        return False


def get_local_lan_ip() -> str:
    candidates = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if is_private_lan_ip(ip) and ip not in candidates:
                candidates.append(ip)
    except Exception:
        pass

    if not candidates:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.168.1.255", 9))
            ip = s.getsockname()[0]
            s.close()
            if is_private_lan_ip(ip):
                candidates.append(ip)
        except Exception:
            pass

    if not candidates:
        return "127.0.0.1"

    def _ip_priority(ip: str) -> int:
        if ip.startswith("192.168.56.") or ip.startswith("172."):
            return 2
        if ip.startswith("192.168.") or ip.startswith("10."):
            return 0
        return 1

    candidates.sort(key=_ip_priority)
    return candidates[0]


def get_broadcast_address(local_ip: str) -> str:
    if not is_private_lan_ip(local_ip):
        return "255.255.255.255"
    parts = local_ip.split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


class WorldLanNode:
    def __init__(
        self,
        device_name: Optional[str] = None,
        os_tag: Optional[str] = None,
        download_dir: Optional[str] = None,
        on_peers_updated: Optional[Callable[[Dict[str, dict]], None]] = None,
        on_file_received: Optional[Callable[[str, str, int, str], None]] = None,
        on_text_received: Optional[Callable[[str, str], None]] = None,
        on_progress: Optional[Callable[[str, float], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.device_name = device_name or platform.node() or "World-Node"
        self.os_tag = os_tag or ("WIN" if os.name == "nt" else "ANDROID")
        self.local_ip = get_local_lan_ip()
        self.download_dir = Path(download_dir or (Path.home() / "Downloads" / "World"))
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.on_peers_updated = on_peers_updated
        self.on_file_received = on_file_received
        self.on_text_received = on_text_received
        self.on_progress = on_progress
        self.on_log = on_log

        self.peers: Dict[str, dict] = {}
        self._running = False
        self._lock = threading.Lock()

    def log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def start(self):
        if self._running:
            return
        self._running = True
        self.local_ip = get_local_lan_ip()
        threading.Thread(target=self._udp_listener, daemon=True).start()
        threading.Thread(target=self._udp_broadcaster, daemon=True).start()
        threading.Thread(target=self._tcp_server, daemon=True).start()
        self.log(f"[NET] Узел запущен: {self.device_name} ({self.local_ip}) • Только LAN (RFC1918)")

    def stop(self):
        self._running = False

    def scan_now(self):
        self._send_beacon()

    def _send_beacon(self):
        if not is_private_lan_ip(self.local_ip):
            return
        payload = json.dumps({
            "magic": "WORLD_DISCOVERY",
            "name": self.device_name,
            "os": self.os_tag,
            "ip": self.local_ip,
            "port": TRANSFER_PORT,
        }).encode("utf-8")

        bcast = get_broadcast_address(self.local_ip)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            sock.sendto(payload, (bcast, DISCOVERY_PORT))
            sock.close()
        except Exception:
            pass

    def _udp_broadcaster(self):
        while self._running:
            self._send_beacon()
            now = time.time()
            changed = False
            with self._lock:
                stale = [ip for ip, info in self.peers.items() if now - info["last_seen"] > 7.0]
                for ip in stale:
                    del self.peers[ip]
                    changed = True
            if changed and self.on_peers_updated:
                self.on_peers_updated(dict(self.peers))
            time.sleep(2.0)

    def _udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", DISCOVERY_PORT))
        except Exception as e:
            self.log(f"[ERR] Ошибка UDP порта {DISCOVERY_PORT}: {e}")
            return
        sock.settimeout(1.0)

        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
                sender_ip = addr[0]
                if not is_private_lan_ip(sender_ip) or sender_ip == self.local_ip:
                    continue
                msg = json.loads(data.decode("utf-8"))
                if msg.get("magic") != "WORLD_DISCOVERY":
                    continue
                with self._lock:
                    is_new = sender_ip not in self.peers
                    self.peers[sender_ip] = {
                        "name": msg.get("name", "Unknown"),
                        "os": msg.get("os", "LAN"),
                        "ip": sender_ip,
                        "port": int(msg.get("port", TRANSFER_PORT)),
                        "last_seen": time.time(),
                    }
                if is_new:
                    self.log(f"[LAN] Обнаружено устройство: {msg.get('name')} ({sender_ip})")
                if self.on_peers_updated:
                    self.on_peers_updated(dict(self.peers))
            except socket.timeout:
                continue
            except Exception:
                continue
        sock.close()

    def _tcp_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", TRANSFER_PORT))
            srv.listen(8)
        except Exception as e:
            self.log(f"[ERR] Ошибка TCP сервера {TRANSFER_PORT}: {e}")
            return
        srv.settimeout(1.0)

        while self._running:
            try:
                conn, addr = srv.accept()
                client_ip = addr[0]
                if not is_private_lan_ip(client_ip):
                    self.log(f"[BLOCK] Отклонено внешнее подключение: {client_ip}")
                    conn.close()
                    continue
                threading.Thread(target=self._handle_client, args=(conn, client_ip), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                continue
        srv.close()

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Соединение разорвано")
            buf.extend(chunk)
        return bytes(buf)

    def _handle_client(self, conn: socket.socket, client_ip: str):
        try:
            magic = self._recv_exact(conn, len(MAGIC_HEADER))
            if magic != MAGIC_HEADER:
                conn.close()
                return
            meta_len = struct.unpack("!I", self._recv_exact(conn, 4))[0]
            meta = json.loads(self._recv_exact(conn, meta_len).decode("utf-8"))
            msg_type = meta.get("type")
            sender_name = meta.get("sender", client_ip)

            if msg_type == "TEXT":
                text_bytes = self._recv_exact(conn, meta["size"])
                text = text_bytes.decode("utf-8")
                conn.sendall(b"OK")
                self.log(f"[CLIPBOARD] Текст от {sender_name}")
                if self.on_text_received:
                    self.on_text_received(sender_name, text)

            elif msg_type == "FILE":
                filename = os.path.basename(meta["filename"])
                total_size = int(meta["size"])
                expected_sha = meta.get("sha256", "")

                save_path = self.download_dir / filename
                counter = 1
                while save_path.exists():
                    stem = Path(filename).stem
                    suffix = Path(filename).suffix
                    save_path = self.download_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                hasher = hashlib.sha256()
                received = 0
                with open(save_path, "wb") as f:
                    while received < total_size:
                        to_read = min(BUFFER_SIZE, total_size - received)
                        chunk = conn.recv(to_read)
                        if not chunk:
                            break
                        f.write(chunk)
                        hasher.update(chunk)
                        received += len(chunk)
                        if self.on_progress and total_size > 0:
                            self.on_progress(filename, received / total_size)

                actual_sha = hasher.hexdigest()
                if expected_sha and actual_sha != expected_sha:
                    conn.sendall(b"HASH_ERR")
                    self.log(f"[ERR] Ошибка контрольной суммы: {filename}")
                else:
                    conn.sendall(b"OK")
                    self.log(f"[FILE] Получен файл: {save_path.name} ({total_size} Б) от {sender_name}")
                    if self.on_file_received:
                        self.on_file_received(sender_name, str(save_path), total_size, actual_sha[:12])
        except Exception as e:
            self.log(f"[ERR] Ошибка приёма данных: {e}")
        finally:
            conn.close()

    def send_text(self, target_ip: str, text: str) -> bool:
        if not is_private_lan_ip(target_ip):
            self.log(f"[BLOCK] Заблокирована попытка отправки в интернет: {target_ip}")
            return False
        data = text.encode("utf-8")
        meta = json.dumps({
            "type": "TEXT",
            "sender": self.device_name,
            "size": len(data),
        }).encode("utf-8")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((target_ip, TRANSFER_PORT))
            sock.sendall(MAGIC_HEADER + struct.pack("!I", len(meta)) + meta + data)
            ack = sock.recv(16)
            sock.close()
            if ack == b"OK":
                self.log(f"[CLIPBOARD] Текст отправлен на {target_ip}")
                return True
        except Exception as e:
            self.log(f"[ERR] Не удалось отправить текст: {e}")
        return False

    def send_file(self, target_ip: str, file_path: str) -> bool:
        if not is_private_lan_ip(target_ip):
            self.log(f"[BLOCK] Заблокирована попытка отправки файла в интернет: {target_ip}")
            return False
        path = Path(file_path)
        if not path.is_file():
            return False

        total_size = path.stat().st_size
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(BUFFER_SIZE):
                hasher.update(chunk)
        sha256_hex = hasher.hexdigest()

        meta = json.dumps({
            "type": "FILE",
            "sender": self.device_name,
            "filename": path.name,
            "size": total_size,
            "sha256": sha256_hex,
        }).encode("utf-8")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15.0)
            sock.connect((target_ip, TRANSFER_PORT))
            sock.sendall(MAGIC_HEADER + struct.pack("!I", len(meta)) + meta)

            sent = 0
            with open(path, "rb") as f:
                while chunk := f.read(BUFFER_SIZE):
                    sock.sendall(chunk)
                    sent += len(chunk)
                    if self.on_progress and total_size > 0:
                        self.on_progress(path.name, sent / total_size)

            ack = sock.recv(16)
            sock.close()
            if ack == b"OK":
                self.log(f"[FILE] Файл {path.name} успешно доставлен на {target_ip}")
                return True
        except Exception as e:
            self.log(f"[ERR] Ошибка передачи файла: {e}")
        return False
