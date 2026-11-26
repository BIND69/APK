"""
World (Android .APK)
Автор и издатель: Tural
Минималистичный тёмно-графитовый интерфейс для локальной передачи файлов и буфера обмена.
Работает строго в локальной сети (RFC1918 LAN Only).
"""

import os
import sys
import threading
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.utils import platform as kivy_platform

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.lan_engine import WorldLanNode

AUTHOR_NAME = "Tural"
APP_VERSION = "1.4.0"
Window.clearcolor = (0.078, 0.090, 0.114, 1)


def request_android_permissions_and_multicast():
    """Запрашивает права на хранилище в Android и включает приём UDP broadcast пакетов."""
    if kivy_platform != "android":
        return None
    try:
        from android.permissions import request_permissions, Permission
        perms = [
            Permission.READ_EXTERNAL_STORAGE,
            Permission.WRITE_EXTERNAL_STORAGE,
        ]
        # Для Android 13+ (API 33+)
        for media_perm in ("READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO"):
            if hasattr(Permission, media_perm):
                perms.append(getattr(Permission, media_perm))
        request_permissions(perms)
    except Exception:
        pass

    # Включаем MulticastLock, чтобы Android не блокировал входящие UDP-маяки от ПК
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        activity = PythonActivity.mActivity
        wifi_manager = activity.getSystemService(Context.WIFI_SERVICE)
        multicast_lock = wifi_manager.createMulticastLock("WorldLanLock")
        multicast_lock.setReferenceCounted(True)
        multicast_lock.acquire()
        return multicast_lock
    except Exception:
        return None


class CardBox(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.padding = 14
        self.spacing = 8
        with self.canvas.before:
            Color(0.110, 0.125, 0.161, 1)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[14])
        self.bind(pos=self._update_rect, size=self._update_rect)

    def _update_rect(self, *_):
        self.rect.pos = self.pos
        self.rect.size = self.size


class WorldAndroidApp(App):
    title = "World"

    def build(self):
        self.selected_ip = ""
        self.peers = {}
        self.multicast_lock = request_android_permissions_and_multicast()

        dl_dir = "/storage/emulated/0/Download/World" if os.path.exists("/storage/emulated/0/Download") else None
        self.node = WorldLanNode(
            device_name="Android-Phone",
            os_tag="ANDROID",
            download_dir=dl_dir,
            on_peers_updated=lambda p: Clock.schedule_once(lambda dt: self.update_peers(p)),
            on_file_received=lambda s, p, sz, h: Clock.schedule_once(lambda dt: self.on_file_done(p, h)),
            on_text_received=lambda s, t: Clock.schedule_once(lambda dt: self.on_text_done(s, t)),
            on_progress=lambda fn, r: Clock.schedule_once(lambda dt: self.on_prog(fn, r)),
            on_log=lambda m: Clock.schedule_once(lambda dt: self.add_log(m)),
        )

        root = BoxLayout(orientation="vertical", padding=14, spacing=12)

        # Верхняя панель
        hdr = CardBox(orientation="horizontal", size_hint_y=None, height=68)
        self.status_lbl = Label(
            text=f"[b]World[/b]  •  LAN: {self.node.local_ip}\n[color=#6EE7B7]Только локальная сеть (WAN отключён)[/color]",
            markup=True, halign="left", valign="middle"
        )
        self.status_lbl.bind(size=self.status_lbl.setter("text_size"))
        hdr.add_widget(self.status_lbl)

        scan_btn = Button(
            text="Поиск", size_hint_x=None, width=90,
            background_normal="", background_color=(0.145, 0.165, 0.212, 1)
        )
        scan_btn.bind(on_release=lambda *_: self.node.scan_now())
        hdr.add_widget(scan_btn)
        root.add_widget(hdr)

        # Карточка устройств
        peers_card = CardBox(orientation="vertical", size_hint_y=None, height=126)
        peers_card.add_widget(Label(text="[b]Устройства в сети (нажмите для выбора):[/b]", markup=True, size_hint_y=None, height=24))
        self.peer_btn = Button(
            text="Поиск ПК в локальной сети...",
            background_normal="", background_color=(0.145, 0.165, 0.212, 1)
        )
        self.peer_btn.bind(on_release=self.cycle_peer)
        peers_card.add_widget(self.peer_btn)
        root.add_widget(peers_card)

        # Карточка отправки файлов
        file_card = CardBox(orientation="vertical", size_hint_y=None, height=136)
        self.prog_lbl = Label(text="Передача файлов (TCP 45455)", size_hint_y=None, height=26)
        file_card.add_widget(self.prog_lbl)

        send_file_btn = Button(
            text="Выбрать и отправить файл на ПК",
            background_normal="", background_color=(0.49, 0.83, 0.99, 1),
            color=(0.06, 0.09, 0.16, 1), bold=True
        )
        send_file_btn.bind(on_release=self.open_file_picker)
        file_card.add_widget(send_file_btn)
        root.add_widget(file_card)

        # Карточка буфера обмена
        clip_card = CardBox(orientation="vertical")
        clip_card.add_widget(Label(text="[b]Локальный буфер обмена:[/b]", markup=True, size_hint_y=None, height=24))
        self.text_box = TextInput(
            hint_text="Введите ссылку или текст для отправки на ПК...",
            background_color=(0.145, 0.165, 0.212, 1),
            foreground_color=(0.89, 0.91, 0.94, 1),
            cursor_color=(0.49, 0.83, 0.99, 1)
        )
        clip_card.add_widget(self.text_box)

        row_btns = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=48)
        send_txt_btn = Button(
            text="Отправить на ПК",
            background_normal="", background_color=(0.43, 0.91, 0.72, 1),
            color=(0.06, 0.09, 0.16, 1), bold=True
        )
        send_txt_btn.bind(on_release=self.send_text)

        copy_btn = Button(
            text="В буфер телефона",
            background_normal="", background_color=(0.145, 0.165, 0.212, 1)
        )
        copy_btn.bind(on_release=lambda *_: Clipboard.copy(self.text_box.text))

        row_btns.add_widget(send_txt_btn)
        row_btns.add_widget(copy_btn)
        clip_card.add_widget(row_btns)
        root.add_widget(clip_card)

        # Лог и авторство
        self.log_lbl = Label(
            text=f"World v{APP_VERSION} • Автор: {AUTHOR_NAME} • Готов к работе в LAN",
            size_hint_y=None, height=34, color=(0.58, 0.64, 0.72, 1)
        )
        root.add_widget(self.log_lbl)

        self.node.start()
        return root

    def update_peers(self, peers: dict):
        self.peers = peers
        if not peers:
            self.selected_ip = ""
            self.peer_btn.text = "Нет активных ПК (проверьте Wi-Fi)"
            return
        if self.selected_ip not in peers:
            self.selected_ip = list(peers.keys())[0]
        info = peers[self.selected_ip]
        self.peer_btn.text = f"[{info['os']}] {info['name']} ({self.selected_ip})"

    def cycle_peer(self, *_):
        if not self.peers:
            self.node.scan_now()
            return
        ips = list(self.peers.keys())
        idx = (ips.index(self.selected_ip) + 1) % len(ips) if self.selected_ip in ips else 0
        self.selected_ip = ips[idx]
        info = self.peers[self.selected_ip]
        self.peer_btn.text = f"[{info['os']}] {info['name']} ({self.selected_ip})"

    def open_file_picker(self, *_):
        if not self.selected_ip:
            self.add_log("Сначала дождитесь обнаружения ПК в сети")
            return
        start_path = "/storage/emulated/0" if os.path.exists("/storage/emulated/0") else os.path.expanduser("~")
        chooser = FileChooserListView(path=start_path)
        box = BoxLayout(orientation="vertical", spacing=8)
        box.add_widget(chooser)
        btn = Button(text="Отправить выбранный файл", size_hint_y=None, height=48)
        box.add_widget(btn)
        popup = Popup(title="Выберите файл (World LAN)", content=box, size_hint=(0.94, 0.88))

        def _send(_):
            if chooser.selection:
                filepath = chooser.selection[0]
                popup.dismiss()
                threading.Thread(target=self.node.send_file, args=(self.selected_ip, filepath), daemon=True).start()

        btn.bind(on_release=_send)
        popup.open()

    def send_text(self, *_):
        if not self.selected_ip or not self.text_box.text.strip():
            return
        txt = self.text_box.text.strip()
        threading.Thread(target=self.node.send_text, args=(self.selected_ip, txt), daemon=True).start()

    def on_prog(self, fn: str, ratio: float):
        self.prog_lbl.text = f"Передача {fn}: {int(ratio * 100)}%"

    def on_file_done(self, path: str, short_sha: str):
        self.prog_lbl.text = f"Сохранено: {os.path.basename(path)} ({short_sha})"

    def on_text_done(self, sender: str, text: str):
        self.text_box.text = text
        Clipboard.copy(text)
        self.add_log(f"Получен текст от {sender}")

    def add_log(self, msg: str):
        self.log_lbl.text = msg


if __name__ == "__main__":
    WorldAndroidApp().run()
