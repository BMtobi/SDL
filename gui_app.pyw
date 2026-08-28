import sys
import os
import re
import random
import queue
import time
import tempfile
import threading
import subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

# Import modular project components
from config import BASE_DIR, CONFIG_FILE, CREDENTIALS_FILE, SETTINGS_FILE, DEFAULT_SETTINGS, HISTORY_FILE
from utils import LazyModule, sanitize_filename, detect_platform, get_binary_path, smart_redirect_url
import database
import workers
import updater

import customtkinter as ctk
from tkinter import messagebox
import tkinter as tk
from PIL import ImageTk

# Token Auto-Sync HTTP handler for Tampermonkey / Chrome extensions
class TokenSyncHandler(BaseHTTPRequestHandler):
    app_instance = None

    def log_message(self, format, *args):
        pass # Suppress default HTTP server stdout logging

    def send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path in ('/', '/index.html', '/remote', '/remote_control.html'):
            html_path = os.path.join(BASE_DIR, "remote_control.html")
            if os.path.exists(html_path):
                try:
                    with open(html_path, 'r', encoding='utf-8') as f:
                        html_content = f.read()
                    self.send_response(200)
                    self.send_cors_headers()
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(html_content.encode('utf-8'))
                    return
                except Exception:
                    pass
        elif self.path == '/api/status':
            if self.app_instance:
                try:
                    now = time.time()
                    tasks_list = []
                    for uid, v in list(self.app_instance.active_tasks.items()):
                        st = v.get("start_time", now)
                        elapsed_secs = int(now - st)
                        tasks_list.append({
                            "uid": uid,
                            "channel_name": v.get("channel_name", ""),
                            "platform": v.get("platform", ""),
                            "status": v.get("status", ""),
                            "progress": v.get("progress", 0.0),
                            "speed": v.get("speed", ""),
                            "size": v.get("size", ""),
                            "elapsed": format_duration_zh(elapsed_secs)
                        })
                    
                    channels_list = []
                    for c in self.app_instance.channels:
                        channels_list.append({
                            "name": c.get("name"),
                            "url": c.get("url"),
                            "record": c.get("record"),
                            "platform": detect_platform(c.get("url", ""))
                        })
                        
                    res_data = {
                        "is_monitoring": self.app_instance.is_monitoring,
                        "channels_count": len(self.app_instance.channels),
                        "lan_ip": self.app_instance.get_current_lan_ip(),
                        "active_tasks": tasks_list,
                        "channels": channels_list
                    }
                    self.send_response(200)
                    self.send_cors_headers()
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps(res_data, ensure_ascii=False).encode('utf-8'))
                    return
                except Exception as e:
                    pass
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
            data = json.loads(post_data.decode('utf-8')) if post_data else {}
            token = data.get('token', '').strip()
            platform = data.get('platform', '').lower()
            
            if self.path == '/api/monitoring/start':
                if self.app_instance:
                    self.app_instance.gui_update_queue.put(("start_monitoring", None))
                    self.app_instance.add_log("🌐 [遠端控制] 收到啟動全域監控指令", "INFO")
                self.send_response(200)
                self.send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'is_monitoring': True}).encode('utf-8'))
                return
            elif self.path == '/api/monitoring/stop':
                if self.app_instance:
                    self.app_instance.gui_update_queue.put(("stop_monitoring", None))
                    self.app_instance.add_log("🌐 [遠端控制] 收到停止全域監控指令", "INFO")
                self.send_response(200)
                self.send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'is_monitoring': False}).encode('utf-8'))
                return
            elif self.path == '/api/manual_download':
                if self.app_instance:
                    url = data.get('url', '').strip()
                    name = data.get('name', '').strip()
                    plat = data.get('platform', '自動偵測')
                    quality = data.get('quality', 'best')
                    fmt = data.get('format', 'mp4')
                    ok, msg = self.app_instance.start_api_manual_download(url, name, plat, quality, fmt)
                    self.send_response(200 if ok else 400)
                    self.send_cors_headers()
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'ok' if ok else 'error', 'message': msg}, ensure_ascii=False).encode('utf-8'))
                    return
            elif self.path in ('/update_withny_token', '/withny_token') or platform == 'withny':
                if token and self.app_instance:
                    if token.startswith("{") or len(token) < 20:
                        self.app_instance.add_log("⚠️ [油猴同步] 收到無效的 Withny Token 格式，已拒絕同步", "WARNING")
                    else:
                        old_token = self.app_instance.settings.get('withny_token', '')
                        if token != old_token:
                            self.app_instance.settings['withny_token'] = token
                            self.app_instance.save_settings()
                            self.app_instance.add_log("✅ [油猴同步] 已透過腳本即時更新 Withny Token！", "SUCCESS")
                            try:
                                self.app_instance.gui_update_queue.put(("refresh_settings_ui", None))
                            except Exception:
                                pass
                        else:
                            self.app_instance.add_log("ℹ️ [油猴同步] 收到 Withny Token (與目前設定一致，無需更新)", "INFO")
                self.send_response(200)
                self.send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
                return
            elif self.path in ('/update_token', '/token'):
                if token and self.app_instance:
                    old_token = self.app_instance.settings.get('rplay_token', '')
                    self.app_instance.settings['rplay_token'] = token
                    self.app_instance.save_settings()
                    
                    from utils import verify_rplay_token
                    is_valid, user_or_err = verify_rplay_token(token, self.app_instance.settings.get('rplay_username'))
                    if is_valid:
                        self.app_instance.add_log(f"✅ [插件同步] Rplay Token 驗證通過 (帳號: {user_or_err})！", "SUCCESS")
                        if getattr(self.app_instance, 'paused_rplay_task', None):
                            task_to_resume = self.app_instance.paused_rplay_task
                            self.app_instance.paused_rplay_task = None
                            self.app_instance.resume_paused_rplay_task(task_to_resume)
                    else:
                        self.app_instance.add_log(f"⚠️ [插件同步] 收到 Rplay Token 但驗證未通過: {user_or_err}", "WARNING")
                        
                    try:
                        self.app_instance.gui_update_queue.put(("refresh_settings_ui", None))
                    except Exception:
                        pass
                
                self.send_response(200)
                self.send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
                return
        except Exception:
            pass
            
        self.send_response(400)
        self.end_headers()

# Helper for duration formatting
def format_duration_zh(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}小時{minutes}分{secs}秒"
    elif minutes > 0:
        return f"{minutes}分{secs}秒"
    else:
        return f"{secs}秒"

class NativeScrollableFrame(tk.Canvas):
    def __init__(self, parent, bg_color, **kwargs):
        self.container = ctk.CTkFrame(parent, fg_color="transparent")
        
        super().__init__(
            self.container, 
            bg=bg_color, 
            bd=0, 
            highlightthickness=0,
            **kwargs
        )
        
        self.scrollbar = ctk.CTkScrollbar(
            self.container, 
            orientation="vertical", 
            command=self.yview
        )
        self.configure(yscrollcommand=self.scrollbar.set)
        
        self.scroll_content = ctk.CTkFrame(self, fg_color="transparent")
        self.window_id = self.create_window((0, 0), window=self.scroll_content, anchor="nw")
        
        super().grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)
        
        self.scroll_content.bind("<Configure>", self._on_content_configure)
        self.bind("<Configure>", self._on_canvas_configure)
        
    def _on_content_configure(self, event):
        self.configure(scrollregion=self.bbox("all"))
        
    def _on_canvas_configure(self, event):
        self.itemconfig(self.window_id, width=event.width)
            
    def yview(self, *args):
        if not args:
            return super().yview()
            
        if args[0] == "moveto":
            try:
                fraction = float(args[1])
                scroll_region = self.cget("scrollregion")
                if scroll_region:
                    coords = [int(c) for c in scroll_region.split()]
                    total_height = coords[3] - coords[1]
                else:
                    bbox = self.bbox("all")
                    total_height = bbox[3] - bbox[1] if bbox else 0
                    
                if total_height > 0:
                    target_pixels = fraction * total_height
                    target_pixels = round(target_pixels)
                    snap_fraction = target_pixels / total_height
                    super().yview("moveto", snap_fraction)
                else:
                    super().yview("moveto", fraction)
            except Exception:
                super().yview("moveto", args[1])
        else:
            super().yview(*args)

    def grid(self, **kwargs):
        self.container.grid(**kwargs)
        
    def grid_forget(self):
        self.container.grid_forget()
        
    def grid_configure(self, **kwargs):
        self.container.grid_configure(**kwargs)

# Add PyInstaller temp path to Windows PATH so subprocesses can find ffmpeg/ffprobe
if hasattr(sys, "_MEIPASS"):
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ["PATH"]

# Set appearance mode
ctk.set_appearance_mode("dark")

# ================= Main GUI Application Class =================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Color Palette Settings (Linear / Raycast Slate Dark Design System)
        self.c_bg = "#0b0f17"                  # Deep Space Slate Background
        self.c_sidebar = "#070a0f"             # Solid Dark Sidebar Foundation
        self.c_frame = "#111722"               # Elevated Surface Container
        self.c_card = "#161e2e"                # Slate Card Base
        self.c_card_hover = "#1e293b"          # Card Hover State
        self.c_card_selected = "#1e283d"       # Selected Card Fill
        self.c_card_border = "#202a3c"         # 1px Subtle Hairline Border
        self.c_card_selected_border = "#38bdf8"# Selected Card Border Highlight
        
        self.c_text_primary = "#f8fafc"        # Slate-50 (Highest contrast)
        self.c_text_secondary = "#94a3b8"      # Slate-400 (Clean metadata/labels)
        self.c_text_muted = "#64748b"          # Slate-500 (Captions, timestamps)
        
        # Primary Brand Accent (Electric Sky / Cyan)
        self.c_accent = "#0284c7"              # Sky-600 Primary Accent
        self.c_accent_hover = "#0369a1"        # Sky-700 Hover
        self.c_accent_light = "#38bdf8"        # Sky-400 Highlight
        
        # Semantic Accents
        self.c_green = "#10b981"               # Emerald-500
        self.c_green_bg = "#064e3b"            # Emerald-950 / Soft badge
        self.c_green_text = "#34d399"          # Emerald-400
        
        self.c_red = "#f43f5e"                 # Rose-500
        self.c_red_bg = "#4c0519"              # Rose-950 / Soft badge
        self.c_red_text = "#fb7185"            # Rose-400
        
        self.c_blue = "#38bdf8"                # Sky-400 (Rplay)
        self.c_blue_bg = "#0c4a6e"             # Sky-950 / Soft badge
        self.c_blue_text = "#7dd3fc"           # Sky-300
        
        self.c_purple = "#a855f7"              # Violet-500 (Withny)
        self.c_purple_bg = "#3b0764"           # Violet-950 / Soft badge
        self.c_purple_text = "#c084fc"         # Violet-400
        
        self.c_yellow = "#f59e0b"              # Amber-500 (FC2)
        self.c_yellow_bg = "#451a03"           # Amber-950 / Soft badge
        self.c_yellow_text = "#fbbf24"         # Amber-400
        
        # Configure root window background
        self.configure(fg_color=self.c_bg)
        
        # Window settings
        self.title("StreamBot - 直播監控與影音下載管理系統")
        self.geometry("1300x830")
        self.minsize(1150, 720)
        
        # State variables
        self.settings = DEFAULT_SETTINGS.copy()
        self.channels = []
        self.history = []
        self.active_tasks = {} # uid -> task_info dict
        self.ended_discord_tasks = set() # uid set of ended tasks to prevent race conditions
        self.discord_ws = None # Discord Gateway websocket client
        self.log_queue = queue.Queue()
        self.gui_update_queue = queue.Queue()
        self.selected_channel_index = -1
        self.is_monitoring = False
        self.monitor_threads = []
        self.settings_dirty = False
        self.channels_dirty = False
        self.channel_image_cache = {} # image url -> CTkImage
        self.current_tab = "dashboard"
        self.channel_card_widgets = {}
        self.local_versions_detected = False
        self.paused_rplay_task = None
        self.thumbnail_queue = queue.Queue()
        for _ in range(2):
            threading.Thread(target=self.worker_thumbnail_downloader, daemon=True).start()
        
        self.platform_colors = {
            "Rplay": (self.c_blue_text, self.c_blue_bg),
            "Withny": (self.c_purple_text, self.c_purple_bg),
            "YouTube": (self.c_red_text, self.c_red_bg),
            "FC2": (self.c_yellow_text, self.c_yellow_bg),
            "Unknown": (self.c_text_secondary, self.c_card)
        }
        
        # Global Universal MouseWheel Scrolling Handler
        self.bind_all("<MouseWheel>", self.handle_universal_mousewheel)
        
        # Load configs
        self.load_all_configs()
        
        # Cleanup leftover temp files in the download directory on startup
        try:
            from utils import clean_dir_leftovers
            if self.settings.get("download_dir"):
                clean_dir_leftovers(self.settings["download_dir"])
        except Exception as e:
            self.add_log(f"啟動時清理暫存檔失敗: {e}", "WARNING")
        
        # Monitor session identifier to prevent duplicate thread race conditions
        self.monitor_session_id = 0

        # Build UI layout
        self.build_ui()
        
        # Start GUI polling
        self.poll_gui_updates()
        
        # Start periodic task timer
        self.start_periodic_task_timer()
        
        # Windows sleep prevention
        self.sleep_prevented_active = None
        self.update_sleep_prevention_state()
        
        # Start Token Auto-Sync HTTP server (port 18730)
        self.start_token_sync_server()
        
        # Start Discord Gateway daemon in background for 24/7 remote command control
        self.discord_gw_thread = None
        self.start_discord_gateway()
        
        # NOTE: Auto-start monitoring on app launch disabled per user request

    def handle_universal_mousewheel(self, event):
        if not event.delta:
            return
        scroll_units = int(-1 * (event.delta / 120))
        
        # Determine target by traversing master hierarchy from event.widget
        curr = event.widget
        while curr:
            # 1. Textbox (tk.Text or CTkTextbox)
            if isinstance(curr, tk.Text):
                try:
                    curr.yview("scroll", scroll_units, "units")
                    return
                except Exception:
                    pass
            elif isinstance(curr, ctk.CTkTextbox):
                try:
                    curr._textbox.yview("scroll", scroll_units, "units")
                    return
                except Exception:
                    pass
                    
            # 2. NativeScrollableFrame canvas or its container
            if isinstance(curr, NativeScrollableFrame):
                try:
                    curr.yview("scroll", scroll_units, "units")
                    return
                except Exception:
                    pass
            elif hasattr(curr, "scroll_content") and isinstance(getattr(curr, "master", None), NativeScrollableFrame):
                try:
                    curr.master.yview("scroll", scroll_units, "units")
                    return
                except Exception:
                    pass
                    
            # 3. CTkScrollableFrame or child inside CTkScrollableFrame
            if isinstance(curr, ctk.CTkScrollableFrame) or hasattr(curr, "_parent_canvas"):
                try:
                    canvas = getattr(curr, "_parent_canvas", None)
                    if canvas:
                        canvas.yview("scroll", scroll_units, "units")
                        return
                except Exception:
                    pass
                    
            curr = getattr(curr, "master", None)

    def start_token_sync_server(self):
        def _run():
            try:
                TokenSyncHandler.app_instance = self
                server = HTTPServer(('0.0.0.0', 18730), TokenSyncHandler)
                self.add_log("🌐 油猴 Token 自動同步服務已在埠號 18730 啟動 (支援同 Wi-Fi 裝置)", "INFO")
                server.serve_forever()
            except Exception as e:
                self.add_log(f"⚠️ 油猴 Token 同步服務啟動失敗: {e}", "WARNING")
                
        threading.Thread(target=_run, daemon=True).start()

    # ================= Bridge functions for modular database =================
    def load_all_configs(self):
        database.load_all_configs(self)
        
    def save_settings(self):
        return database.save_settings(self)
        
    def sync_withny_credentials(self):
        database.sync_withny_credentials(self)
        
    def load_channels(self):
        database.load_channels(self)
        
    def write_channels_file(self):
        return database.write_channels_file(self)
        
    def add_history_entry(self, channel_name, platform, title, file_path):
        database.add_history_entry(self, channel_name, platform, title, file_path)

    # ================= Logging System =================
    def add_log(self, message, level="INFO"):
        level = level.upper()
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] [{level}] {message}"
        self.log_queue.put((log_msg, level))
        try:
            print(log_msg)
        except Exception:
            try:
                print(log_msg.encode("utf-8", errors="replace").decode("cp950", errors="replace"))
            except Exception:
                pass

    # ================= UI Build & Navigation =================
    def build_ui(self):
        # Grid layout config
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0) # Sidebar
        self.grid_columnconfigure(1, weight=1) # Main Viewport Container
        
        # 1. Left Sidebar Frame (Linear / Raycast Dark Foundation)
        sidebar = ctk.CTkFrame(self, width=225, fg_color=self.c_sidebar, corner_radius=0, border_color=self.c_card_border, border_width=1)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(8, weight=1)
        
        # Logo & Brand Area
        brand_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand_frame.grid(row=0, column=0, padx=16, pady=(20, 15), sticky="ew")
        
        logo_lbl = ctk.CTkLabel(
            brand_frame, 
            text="⚡ STREAMBOT", 
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=18, weight="bold"), 
            text_color=self.c_text_primary
        )
        logo_lbl.pack(anchor="w")
        
        sub_logo_lbl = ctk.CTkLabel(
            brand_frame,
            text="MONITOR & ARCHIVE STUDIO",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=9, weight="bold"),
            text_color=self.c_text_muted
        )
        sub_logo_lbl.pack(anchor="w", pady=(1, 6))
        
        # Live System Status Badge in Sidebar
        self.sidebar_status_badge = ctk.CTkLabel(
            brand_frame,
            text="● 系統就緒 (待機中)",
            fg_color=self.c_frame,
            text_color=self.c_green_text,
            corner_radius=6,
            height=22,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold")
        )
        self.sidebar_status_badge.pack(fill="x", pady=(2, 0))
        
        # Nav buttons (Pill shape styled active status)
        tabs = [
            ("dashboard", "📊  頻道監控"),
            ("tasks", "📥  下載任務"),
            ("history", "📜  下載紀錄"),
            ("manual", "🔗  手動下載"),
            ("settings", "⚙️  系統設定"),
            ("updates", "🔄  元件更新"),
            ("logs", "📝  系統日誌")
        ]
        
        self.nav_buttons = {}
        for idx, (tab_id, title) in enumerate(tabs):
            btn = ctk.CTkButton(
                sidebar,
                text=title,
                height=40,
                anchor="w",
                fg_color="#1e293b" if tab_id == "dashboard" else "transparent",
                text_color=self.c_accent_light if tab_id == "dashboard" else self.c_text_secondary,
                font=ctk.CTkFont(family="Segoe UI Variable Text", size=13, weight="bold"),
                hover_color="#1e293b" if tab_id == "dashboard" else "#131b27",
                corner_radius=8,
                command=lambda tid=tab_id: self.select_tab(tid)
            )
            btn.grid(row=idx+1, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[tab_id] = btn
            
        # Left Bottom Monitoring Control Block
        self.monitor_btn = ctk.CTkButton(
            sidebar, 
            text="▶️ 啟動全域監控", 
            height=44, 
            fg_color=self.c_accent, 
            hover_color=self.c_accent_hover, 
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=13, weight="bold"),
            corner_radius=8,
            command=self.toggle_monitoring
        )
        self.monitor_btn.grid(row=9, column=0, padx=12, pady=16, sticky="ew")
        
        # 2. Main Viewport Container
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, padx=16, pady=16, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)
        
        self.frames = {}
        
        # Build and mount all viewport panels
        self.build_dashboard_tab()
        self.build_tasks_tab()
        self.build_history_tab()
        self.build_manual_tab()
        self.build_settings_tab()
        self.build_updates_tab()
        self.build_logs_tab()
        
        # Render default tab (Dashboard)
        self.select_tab("dashboard")

    def select_tab(self, tab_id):
        # Check settings dirty state
        if hasattr(self, "current_tab") and self.current_tab == "settings" and tab_id != "settings" and getattr(self, "settings_dirty", False):
            ans = messagebox.askyesnocancel("未儲存的變更", "您的設定已變更，但尚未儲存！\n是否要在離開前儲存設定？")
            if ans is True: # Yes
                self.apply_and_save_settings_gui()
            elif ans is False: # No
                # Discard changes
                self.reset_settings_fields_from_state()
                self.settings_dirty = False
            else: # Cancel (None)
                self.nav_buttons["settings"].configure(fg_color="#1e293b", text_color=self.c_accent_light)
                if tab_id in self.nav_buttons:
                    self.nav_buttons[tab_id].configure(fg_color="transparent", text_color=self.c_text_secondary)
                return
                
        # Check channels dirty state
        if hasattr(self, "current_tab") and self.current_tab == "dashboard" and tab_id != "dashboard" and getattr(self, "channels_dirty", False):
            ans = messagebox.askyesnocancel("未儲存的變更", "您的頻道清單已變更，但尚未儲存到 channels.json！\n是否要現在儲存變更？")
            if ans is True: # Yes
                self.write_channels_file()
            elif ans is False: # No
                self.load_channels()
                self.refresh_channel_list_ui()
                self.channels_dirty = False
            else: # Cancel (None)
                self.nav_buttons["dashboard"].configure(fg_color="#1e293b", text_color=self.c_accent_light)
                if tab_id in self.nav_buttons:
                    self.nav_buttons[tab_id].configure(fg_color="transparent", text_color=self.c_text_secondary)
                return
                
        self.current_tab = tab_id

        # Visual feedback on sidebar buttons matching the active status (Linear Slate Accent)
        for tid, btn in self.nav_buttons.items():
            if tid == tab_id:
                btn.configure(fg_color="#1e293b", text_color=self.c_accent_light, hover_color="#1e293b")
            else:
                btn.configure(fg_color="transparent", text_color=self.c_text_secondary, hover_color="#131b27")
                
        # Hide all frames and show targeted one
        for frame in self.frames.values():
            frame.grid_forget()
            
        self.frames[tab_id].grid(row=0, column=0, sticky="nsew")
        
        # Trigger explicit refreshes
        if tab_id == "tasks":
            self.refresh_tasks_ui()
        elif tab_id == "history":
            self.refresh_history_ui()
        elif tab_id == "updates":
            if not getattr(self, "local_versions_detected", False):
                self.local_versions_detected = True
                threading.Thread(target=self.detect_local_versions_async, daemon=True).start()

    # ================= Dashboard Tab =================
    def build_dashboard_tab(self):
        dash_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["dashboard"] = dash_frame
        dash_frame.grid_columnconfigure(0, weight=3)
        dash_frame.grid_columnconfigure(1, weight=1)
        dash_frame.grid_rowconfigure(0, weight=1)
        
        # Left: Channels Monitor List Panel
        list_panel = ctk.CTkFrame(
            dash_frame, 
            fg_color=self.c_frame, 
            border_color=self.c_card_border, 
            border_width=1, 
            corner_radius=12
        )
        list_panel.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="nsew")
        list_panel.grid_rowconfigure(2, weight=1)
        list_panel.grid_columnconfigure(0, weight=1)
        self.list_panel = list_panel
        
        # Top KPI Telemetry Banner (4 Metric Tiles)
        metrics_bar = ctk.CTkFrame(list_panel, fg_color="transparent")
        metrics_bar.grid(row=0, column=0, padx=12, pady=(12, 4), sticky="ew")
        for c in range(4):
            metrics_bar.grid_columnconfigure(c, weight=1)
            
        def create_kpi_card(parent, col, title, initial_val, icon, accent_col):
            card = ctk.CTkFrame(parent, fg_color=self.c_sidebar, border_color=self.c_card_border, border_width=1, corner_radius=8, height=60)
            card.grid(row=0, column=col, padx=4, pady=0, sticky="ew")
            card.grid_propagate(False)
            
            # Left icon
            icn_lbl = ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=16))
            icn_lbl.pack(side="left", padx=(12, 8), pady=10)
            
            # Right text container (title + value with balanced margins)
            text_container = ctk.CTkFrame(card, fg_color="transparent")
            text_container.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=(7, 7))
            
            t_lbl = ctk.CTkLabel(text_container, text=title, font=ctk.CTkFont(family="Segoe UI Variable Text", size=10, weight="bold"), text_color=self.c_text_muted, anchor="w")
            t_lbl.pack(anchor="w", pady=(0, 1))
            
            v_lbl = ctk.CTkLabel(text_container, text=initial_val, font=ctk.CTkFont(family="Segoe UI Variable Text", size=13, weight="bold"), text_color=accent_col, anchor="w")
            v_lbl.pack(anchor="w", pady=(0, 0))
            return v_lbl

        self.kpi_total_chan = create_kpi_card(metrics_bar, 0, "監控頻道", f"{len(self.channels)} 個", "📡", self.c_text_primary)
        self.kpi_live_chan = create_kpi_card(metrics_bar, 1, "直播開台", "0 個", "🔴", self.c_green_text)
        self.kpi_active_tasks = create_kpi_card(metrics_bar, 2, "錄製任務", "0 個", "⚡", self.c_blue_text)
        self.kpi_sys_status = create_kpi_card(metrics_bar, 3, "監控狀態", "待機中", "🛡️", self.c_text_secondary)
        
        # List Panel Search Bar
        search_frame = ctk.CTkFrame(list_panel, fg_color="transparent")
        search_frame.grid(row=1, column=0, padx=12, pady=(8, 8), sticky="ew")
        search_frame.grid_columnconfigure(1, weight=1)
        search_frame.grid_columnconfigure(2, weight=0)
        
        search_label = ctk.CTkLabel(search_frame, text="🔍 搜尋：", text_color=self.c_text_secondary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"))
        search_label.grid(row=0, column=0, padx=(4, 6), pady=0)
        self.search_entry = ctk.CTkEntry(
            search_frame, 
            placeholder_text="輸入名稱或網址以進行過濾...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            border_width=1,
            height=34,
            corner_radius=8,
            text_color=self.c_text_primary,
            placeholder_text_color=self.c_text_muted
        )
        self.search_entry.grid(row=0, column=1, padx=4, pady=0, sticky="ew")
        self.search_entry.bind("<KeyRelease>", self.filter_channels)

        self.toggle_edit_btn = ctk.CTkButton(
            search_frame,
            text="🛠 隱藏設定面板",
            fg_color="#1e293b",
            hover_color="#334155",
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            height=34,
            corner_radius=8,
            width=110,
            command=self.toggle_edit_panel
        )
        self.toggle_edit_btn.grid(row=0, column=2, padx=(10, 4), pady=0)
        self.edit_panel_visible = True
        
        # Channels Scrollable Frame
        self.channels_scroll_frame = NativeScrollableFrame(list_panel, self.c_frame)
        self.channels_scroll_frame.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="nsew")
        
        # Channel Card Container
        self.channel_cards = [] 
        self.refresh_channel_list_ui()
        
        # Right: Channel Attributes Editing Panel
        edit_panel = ctk.CTkFrame(
            dash_frame, 
            fg_color=self.c_frame, 
            border_color=self.c_card_border, 
            border_width=1, 
            corner_radius=12
        )
        edit_panel.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")
        edit_panel.grid_rowconfigure(7, weight=1)
        edit_panel.grid_columnconfigure(0, weight=1)
        self.edit_panel = edit_panel
        
        title_label = ctk.CTkLabel(edit_panel, text="🛠 頻道屬性設定", font=ctk.CTkFont(family="Segoe UI Variable Text", size=15, weight="bold"), text_color=self.c_text_primary)
        title_label.grid(row=0, column=0, padx=16, pady=(16, 12), sticky="w")
        
        # Form Fields
        fields_frame = ctk.CTkFrame(edit_panel, fg_color="transparent")
        fields_frame.grid(row=1, column=0, padx=16, pady=0, sticky="ew")
        fields_frame.grid_columnconfigure(1, weight=1)
        
        # Name
        lbl1 = ctk.CTkLabel(fields_frame, text="顯示名稱 (ID):", text_color=self.c_text_secondary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12))
        lbl1.grid(row=0, column=0, padx=4, pady=5, sticky="w")
        self.chan_name_entry = ctk.CTkEntry(
            fields_frame, 
            placeholder_text="例如: セラ",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            height=34,
            corner_radius=6,
            text_color=self.c_text_primary,
            placeholder_text_color=self.c_text_muted
        )
        self.chan_name_entry.grid(row=0, column=1, padx=4, pady=5, sticky="ew")
        
        # URL
        lbl2 = ctk.CTkLabel(fields_frame, text="頻道網址 (URL):", text_color=self.c_text_secondary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12))
        lbl2.grid(row=1, column=0, padx=4, pady=5, sticky="w")
        self.chan_url_entry = ctk.CTkEntry(
            fields_frame, 
            placeholder_text="輸入頻道直播或首頁網址...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            height=34,
            corner_radius=6,
            text_color=self.c_text_primary,
            placeholder_text_color=self.c_text_muted
        )
        self.chan_url_entry.grid(row=1, column=1, padx=4, pady=5, sticky="ew")
        self.chan_url_entry.bind("<KeyRelease>", self.on_chan_url_keyrelease)
        
        # Detected platform badge
        self.chan_platform_badge = ctk.CTkLabel(fields_frame, text="平台識別：未知 ⚪", font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"), text_color=self.c_text_secondary)
        self.chan_platform_badge.grid(row=2, column=0, columnspan=2, padx=4, pady=6, sticky="w")
        
        # Archive Enable Toggle
        self.chan_record_var = ctk.BooleanVar(value=True)
        self.chan_record_switch = ctk.CTkSwitch(
            fields_frame, 
            text="啟用自動錄影功能", 
            variable=self.chan_record_var,
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            progress_color=self.c_accent,
            button_color=self.c_text_primary,
            button_hover_color=self.c_accent_hover
        )
        self.chan_record_switch.grid(row=3, column=0, columnspan=2, padx=4, pady=6, sticky="w")
        
        # Cover image URL
        lbl3 = ctk.CTkLabel(fields_frame, text="封面圖片網址:", text_color=self.c_text_secondary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12))
        lbl3.grid(row=4, column=0, padx=4, pady=5, sticky="w")
        self.chan_image_entry = ctk.CTkEntry(
            fields_frame, 
            placeholder_text="網址 (用於 Discord 通知)...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            height=34,
            corner_radius=6,
            text_color=self.c_text_primary,
            placeholder_text_color=self.c_text_muted
        )
        self.chan_image_entry.grid(row=4, column=1, padx=4, pady=5, sticky="ew")
        self.chan_image_entry.bind("<KeyRelease>", self.on_chan_image_keyrelease)
        
        # Image Preview Area
        preview_group = ctk.CTkFrame(edit_panel, fg_color=self.c_sidebar, border_color=self.c_card_border, border_width=1, corner_radius=8)
        preview_group.grid(row=2, column=0, padx=16, pady=12, sticky="nsew")
        preview_group.grid_rowconfigure(1, weight=1)
        preview_group.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(
            preview_group, 
            text="🖼 封面預覽", 
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
            text_color=self.c_text_muted
        ).grid(row=0, column=0, padx=10, pady=4, sticky="w")
        
        self.image_preview_label = ctk.CTkLabel(preview_group, text="無圖片預覽", text_color=self.c_text_secondary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=11))
        self.image_preview_label.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        
        # Action Buttons for Editing Panel
        btn_frame = ctk.CTkFrame(edit_panel, fg_color="transparent")
        btn_frame.grid(row=3, column=0, padx=16, pady=3, sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        
        self.add_chan_btn = ctk.CTkButton(
            btn_frame, 
            text="✨ 新增頻道", 
            fg_color=self.c_green, 
            hover_color="#059669", 
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            height=34,
            corner_radius=6,
            command=self.add_channel
        )
        self.add_chan_btn.grid(row=0, column=0, padx=3, pady=3, sticky="ew")
        
        self.update_chan_btn = ctk.CTkButton(
            btn_frame, 
            text="💾 更新選取", 
            state="disabled", 
            fg_color=self.c_accent,
            hover_color=self.c_accent_hover,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            height=34,
            corner_radius=6,
            command=self.update_channel
        )
        self.update_chan_btn.grid(row=0, column=1, padx=3, pady=3, sticky="ew")
        
        btn_frame2 = ctk.CTkFrame(edit_panel, fg_color="transparent")
        btn_frame2.grid(row=4, column=0, padx=16, pady=3, sticky="ew")
        btn_frame2.grid_columnconfigure(0, weight=1)
        btn_frame2.grid_columnconfigure(1, weight=1)
        
        self.delete_chan_btn = ctk.CTkButton(
            btn_frame2, 
            text="🗑 刪除選取", 
            fg_color=self.c_red, 
            hover_color="#e11d48", 
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            height=34,
            corner_radius=6,
            state="disabled", 
            command=self.delete_channel
        )
        self.delete_chan_btn.grid(row=0, column=0, padx=3, pady=3, sticky="ew")
        
        self.clear_chan_btn = ctk.CTkButton(
            btn_frame2, 
            text="🧹 清空輸入", 
            fg_color="#1e293b", 
            hover_color="#334155", 
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            height=34,
            corner_radius=6,
            command=self.clear_channel_form
        )
        self.clear_chan_btn.grid(row=0, column=1, padx=3, pady=3, sticky="ew")
        
        # Save Channels button at bottom
        self.save_chans_btn = ctk.CTkButton(
            edit_panel, 
            text="💾 儲存寫入 channels.json", 
            height=38, 
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=13, weight="bold"), 
            fg_color=self.c_accent, 
            hover_color=self.c_accent_hover,
            corner_radius=8,
            command=self.write_channels_file
        )
        self.save_chans_btn.grid(row=6, column=0, padx=16, pady=16, sticky="ew")

    def select_channel_row_by_url(self, url):
        found_idx = -1
        for idx, chan in enumerate(self.channels):
            if chan["url"] == url:
                found_idx = idx
                break
        if found_idx != -1:
            self.select_channel_row(found_idx)

    def refresh_channel_list_ui(self):
        # Build active and inactive lists based on channels
        active_chans = []
        inactive_chans = []
        current_filtered_urls = set()
        
        for idx, channel in enumerate(self.channels):
            # Determine channel active status
            channel_state = "Offline"
            for utask in self.active_tasks.values():
                # Match by URL first, then fallback to name
                task_url = utask.get("channel_url") or utask.get("url")
                if task_url:
                    if task_url == channel["url"]:
                        channel_state = utask.get("status", "Offline")
                        break
                elif utask.get("channel_name") == channel["name"]:
                    channel_state = utask.get("status", "Offline")
                    break
            
            is_active_download = channel_state in ["錄影中", "下載中", "準備錄影", "解析連線中..."]
            
            # Filter logic (search query)
            q = self.search_entry.get().lower().strip()
            if q:
                if q not in channel["name"].lower() and q not in channel["url"].lower():
                    continue
                    
            current_filtered_urls.add(channel["url"])
            if is_active_download:
                active_chans.append((idx, channel, channel_state, True))
            else:
                inactive_chans.append((idx, channel, channel_state, False))
                
        # Rebuild cards in sorted order (active first, then inactive)
        sorted_list = active_chans + inactive_chans
        
        # Update Dashboard Top KPI Telemetry
        live_count = sum(1 for _, _, state, is_act in sorted_list if is_act or "錄影中" in state or "下載中" in state)
        active_task_count = len([t for t in self.active_tasks.values() if t.get("status") != "監控中"])
        if hasattr(self, "kpi_total_chan"):
            self.kpi_total_chan.configure(text=f"{len(self.channels)} 個")
        if hasattr(self, "kpi_live_chan"):
            self.kpi_live_chan.configure(text=f"{live_count} 個", text_color=self.c_green_text if live_count > 0 else self.c_text_muted)
        if hasattr(self, "kpi_active_tasks"):
            self.kpi_active_tasks.configure(text=f"{active_task_count} 個", text_color=self.c_blue_text if active_task_count > 0 else self.c_text_muted)
        if hasattr(self, "kpi_sys_status"):
            self.kpi_sys_status.configure(
                text="● 監控中" if self.is_monitoring else "○ 待機中", 
                text_color=self.c_green_text if self.is_monitoring else self.c_text_muted
            )
        
        # Destroy cards that are no longer in the filtered list
        if not hasattr(self, "channel_card_widgets"):
            self.channel_card_widgets = {}
            
        existing_urls = list(self.channel_card_widgets.keys())
        for url in existing_urls:
            if url not in current_filtered_urls:
                try:
                    self.channel_card_widgets[url]["card"].destroy()
                except Exception:
                    pass
                self.channel_card_widgets.pop(url)
                
        # Unpack all remaining cards so we can repack them in the new sorted order
        for card_info in self.channel_card_widgets.values():
            try:
                card_info["card"].pack_forget()
            except Exception:
                pass
                
        self.channel_cards = []
        
        for idx_in_channels, channel, channel_state, is_active_download in sorted_list:
            url = channel["url"]
            img_url = channel.get("image", "").strip()
            platform = detect_platform(url)
            
            # Determine badge colors and text
            plat_color, plat_bg = self.platform_colors.get(platform, (self.c_text_secondary, self.c_card))
            if channel_state in ["錄影中", "下載中"]:
                rec_text = f"🔴 {channel_state}"
                rec_color = self.c_red_text
                rec_bg = self.c_red_bg
            elif is_active_download or "監控中" in channel_state:
                rec_text = f"🟢 {channel_state}"
                rec_color = self.c_green_text
                rec_bg = self.c_green_bg
            else:
                rec_text = "REC ON" if channel["record"] else "NOTIFY"
                rec_color = self.c_green_text if channel["record"] else self.c_text_muted
                rec_bg = self.c_green_bg if channel["record"] else self.c_sidebar
                
            card_fg = self.c_card_selected if idx_in_channels == self.selected_channel_index else self.c_card
            card_border = self.c_card_selected_border if idx_in_channels == self.selected_channel_index else self.c_card_border
            
            if url in self.channel_card_widgets:
                # Update existing widgets in-place
                card_info = self.channel_card_widgets[url]
                card = card_info["card"]
                card.configure(bg=card_fg, highlightbackground=card_border)
                
                card_info["name_label"].configure(text=channel["name"], bg=card_fg)
                
                url_subtitle = channel["url"]
                if len(url_subtitle) > 42:
                    url_subtitle = url_subtitle[:39] + "..."
                card_info["url_label"].configure(text=url_subtitle, bg=card_fg)
                
                card_info["plat_badge"].configure(text=platform.upper(), bg=plat_bg, fg=plat_color)
                card_info["rec_badge"].configure(text=rec_text, bg=rec_bg, fg=rec_color)
                
                card_info["thumb_container"].configure(bg=self.c_sidebar)
                
                thumbnail_label = card_info["thumbnail"]
                thumbnail_label.configure(bg=self.c_sidebar)
                if img_url:
                    if img_url in self.channel_image_cache:
                        img_data = self.channel_image_cache[img_url]
                        if img_data == "loading":
                            thumbnail_label.configure(text="⏳", image="", bg=self.c_sidebar)
                        elif img_data == "failed":
                            thumbnail_label.configure(text="📺", image="", bg=self.c_sidebar)
                        else:
                            thumbnail_label.configure(image=img_data, text="", bg=self.c_sidebar)
                    else:
                        thumbnail_label.configure(text="⏳", image="", bg=self.c_sidebar)
                        self.fetch_channel_thumbnail_async(img_url)
                else:
                    thumbnail_label.configure(text="📺", image="", bg=self.c_sidebar)
            else:
                # Create a new card (Standard tk widgets for scrolling speed)
                card = tk.Frame(
                    self.channels_scroll_frame.scroll_content,
                    bg=card_fg,
                    bd=0,
                    relief="flat",
                    highlightthickness=1,
                    highlightbackground=card_border,
                    highlightcolor=card_border
                )
                card.grid_columnconfigure(1, weight=1)
                
                # 1. Left Thumbnail
                thumb_container = tk.Frame(card, width=44, height=44, bg=self.c_sidebar)
                thumb_container.grid_propagate(False)
                thumb_container.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=10)
                
                thumbnail_label = tk.Label(thumb_container, text="", bg=self.c_sidebar, fg=self.c_text_primary)
                thumbnail_label.pack(expand=True, fill="both")
                
                if img_url:
                    if img_url in self.channel_image_cache:
                        img_data = self.channel_image_cache[img_url]
                        if img_data == "loading":
                            thumbnail_label.configure(text="⏳", image="", bg=self.c_sidebar)
                        elif img_data == "failed":
                            thumbnail_label.configure(text="📺", image="", bg=self.c_sidebar)
                        else:
                            thumbnail_label.configure(image=img_data, text="", bg=self.c_sidebar)
                    else:
                        thumbnail_label.configure(text="⏳", image="", bg=self.c_sidebar)
                        self.fetch_channel_thumbnail_async(img_url)
                else:
                    thumbnail_label.configure(text="📺", image="", bg=self.c_sidebar)
                    
                # 2. Text Labels gridded directly onto card
                name_label = tk.Label(
                    card, 
                    text=channel["name"], 
                    font=("Segoe UI Variable Text", 10, "bold"),
                    bg=card_fg,
                    fg=self.c_text_primary,
                    anchor="w"
                )
                name_label.grid(row=0, column=1, padx=(4, 10), pady=(10, 2), sticky="w")
                
                url_subtitle = channel["url"]
                if len(url_subtitle) > 42:
                    url_subtitle = url_subtitle[:39] + "..."
                url_label = tk.Label(
                    card, 
                    text=url_subtitle, 
                    font=("Segoe UI Variable Text", 9),
                    bg=card_fg,
                    fg=self.c_text_secondary,
                    anchor="w"
                )
                url_label.grid(row=1, column=1, padx=(4, 10), pady=(2, 10), sticky="w")
                
                # 3. Platform Badge
                plat_badge = tk.Label(
                    card, 
                    text=platform.upper(), 
                    bg=plat_bg,
                    fg=plat_color, 
                    font=("Segoe UI Variable Text", 8, "bold"),
                    padx=8,
                    pady=3,
                    relief="flat"
                )
                plat_badge.grid(row=0, column=2, rowspan=2, padx=6, pady=10)
                
                # 4. Record Badge
                rec_badge = tk.Label(
                    card, 
                    text=rec_text, 
                    bg=rec_bg,
                    fg=rec_color, 
                    font=("Segoe UI Variable Text", 8, "bold"),
                    padx=8,
                    pady=3,
                    relief="flat"
                )
                rec_badge.grid(row=0, column=3, rowspan=2, padx=(4, 10), pady=10)
                
                # Selection bindings using URL instead of index to prevent index shifts
                for widget in [card, name_label, url_label, thumbnail_label]:
                    widget.bind("<Button-1>", lambda event, u=url: self.select_channel_row_by_url(u))
                    
                self.channel_card_widgets[url] = {
                    "card": card,
                    "thumb_container": thumb_container,
                    "thumbnail": thumbnail_label,
                    "name_label": name_label,
                    "url_label": url_label,
                    "plat_badge": plat_badge,
                    "rec_badge": rec_badge
                }
                
            card.pack(fill="x", padx=4, pady=3)
            self.channel_cards.append(card)

    def select_channel_row(self, index):
        self.selected_channel_index = index
        channel = self.channels[index]
        
        # Load details to form
        self.chan_name_entry.delete(0, "end")
        self.chan_name_entry.insert(0, channel["name"])
        
        self.chan_url_entry.delete(0, "end")
        self.chan_url_entry.insert(0, channel["url"])
        
        self.chan_record_var.set(channel["record"])
        
        self.chan_image_entry.delete(0, "end")
        self.chan_image_entry.insert(0, channel["image"])
        
        # Refresh colors and selection indicators in card lists via URL dictionary lookup
        for i, chan in enumerate(self.channels):
            card_info = self.channel_card_widgets.get(chan["url"])
            if card_info:
                card = card_info["card"]
                bg_col = self.c_card_selected if i == index else self.c_card
                border_col = self.c_card_selected_border if i == index else self.c_card_border
                card.configure(bg=bg_col, highlightbackground=border_col)
                card_info["name_label"].configure(bg=bg_col)
                card_info["url_label"].configure(bg=bg_col)
                
        # Enable update and delete buttons
        self.update_chan_btn.configure(state="normal")
        self.delete_chan_btn.configure(state="normal")
        
        # Update previews
        self.update_detected_platform_badge()
        self.trigger_image_preview(channel["image"])

    def on_chan_url_keyrelease(self, event):
        self.update_detected_platform_badge()

    def update_detected_platform_badge(self):
        url = self.chan_url_entry.get().strip()
        plat = detect_platform(url)
        color = self.platform_colors.get(plat, (self.c_text_secondary, self.c_card))[0]
        self.chan_platform_badge.configure(text=f"平台識別：{plat}", text_color=color)

    def on_chan_image_keyrelease(self, event):
        url = self.chan_image_entry.get().strip()
        self.trigger_image_preview(url)

    def trigger_image_preview(self, url):
        if not url:
            self.image_preview_label.configure(text="無圖片預覽", image=None)
            return
            
        def fetch_image():
            try:
                self.image_preview_label.configure(text="載入預覽中...")
                import requests
                from PIL import Image
                response = requests.get(url, timeout=5, stream=True)
                if response.status_code == 200:
                    img = Image.open(response.raw)
                    img.thumbnail((150, 150))
                    photo = ctk.CTkImage(img, size=(img.width, img.height))
                    self.gui_update_queue.put(("image_preview_success", photo))
                else:
                    self.gui_update_queue.put(("image_preview_fail", "無法取得圖片"))
            except Exception as e:
                self.gui_update_queue.put(("image_preview_fail", "圖片載入失敗"))
                
        threading.Thread(target=fetch_image, daemon=True).start()

    def worker_thumbnail_downloader(self):
        while True:
            img_url = self.thumbnail_queue.get()
            try:
                import io
                import requests
                from PIL import Image, ImageTk
                response = requests.get(img_url, timeout=5)
                if response.status_code == 200:
                    img = Image.open(io.BytesIO(response.content))
                    img = img.resize((45, 45), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self.channel_image_cache[img_url] = photo
                    self.gui_update_queue.put(("thumbnail_loaded", (img_url, photo)))
                else:
                    self.channel_image_cache[img_url] = "failed"
            except Exception:
                self.channel_image_cache[img_url] = "failed"
            finally:
                self.thumbnail_queue.task_done()

    def fetch_channel_thumbnail_async(self, img_url):
        if img_url in self.channel_image_cache:
            return
        self.channel_image_cache[img_url] = "loading"
        self.thumbnail_queue.put(img_url)

    def clear_channel_form(self):
        self.chan_name_entry.delete(0, "end")
        self.chan_url_entry.delete(0, "end")
        self.chan_image_entry.delete(0, "end")
        self.chan_record_var.set(True)
        self.image_preview_label.configure(text="無圖片預覽", image=None)
        self.selected_channel_index = -1
        
        # Deselect cards
        for card in self.channel_cards:
            card.configure(fg_color=self.c_card, border_color=self.c_card_border)
            
        self.update_chan_btn.configure(state="disabled")
        self.delete_chan_btn.configure(state="disabled")
        self.update_detected_platform_badge()

    def add_channel(self):
        name = self.chan_name_entry.get().strip()
        url = self.chan_url_entry.get().strip()
        url = smart_redirect_url(url)
        self.chan_url_entry.delete(0, 'end')
        self.chan_url_entry.insert(0, url)
        
        record = self.chan_record_var.get()
        image = self.chan_image_entry.get().strip()
        
        if not name or not url:
            self.add_log("新增失敗：名稱與網址不能為空！", "WARNING")
            return
            
        new_chan = {
            "name": name,
            "url": url,
            "record": record,
            "image": image
        }
        
        self.channels.append(new_chan)
        self.refresh_channel_list_ui()
        self.clear_channel_form()
        self.channels_dirty = True
        self.add_log(f"已新增頻道「{name}」(尚未寫入，請點擊儲存變更)")

    def update_channel(self):
        if self.selected_channel_index == -1:
            return
            
        name = self.chan_name_entry.get().strip()
        url = self.chan_url_entry.get().strip()
        url = smart_redirect_url(url)
        self.chan_url_entry.delete(0, 'end')
        self.chan_url_entry.insert(0, url)
        
        record = self.chan_record_var.get()
        image = self.chan_image_entry.get().strip()
        
        if not name or not url:
            self.add_log("更新失敗：名稱與網址不能為空！", "WARNING")
            return
            
        self.channels[self.selected_channel_index] = {
            "name": name,
            "url": url,
            "record": record,
            "image": image
        }
        
        self.refresh_channel_list_ui()
        self.select_channel_row(self.selected_channel_index)
        self.channels_dirty = True
        self.add_log(f"已更新頻道「{name}」(請點擊儲存變更)")

    def delete_channel(self):
        if self.selected_channel_index == -1:
            return
            
        channel = self.channels[self.selected_channel_index]
        self.channels.pop(self.selected_channel_index)
        self.refresh_channel_list_ui()
        self.clear_channel_form()
        self.channels_dirty = True
        self.add_log(f"已移除頻道「{channel['name']}」(請點擊儲存變更)")

    def toggle_edit_panel(self):
        if getattr(self, "edit_panel_visible", True):
            self.edit_panel.grid_forget()
            self.list_panel.grid_configure(columnspan=2, padx=0)
            self.toggle_edit_btn.configure(text="🛠 顯示設定面板", fg_color=self.c_accent, hover_color=self.c_accent_hover)
            self.edit_panel_visible = False
        else:
            self.list_panel.grid_configure(columnspan=1, padx=(0, 8))
            self.edit_panel.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")
            self.toggle_edit_btn.configure(text="🛠 隱藏設定面板", fg_color="gray30", hover_color="gray40")
            self.edit_panel_visible = True

    def filter_channels(self, event=None):
        self.refresh_channel_list_ui()

    # ================= Tasks Tab =================
    def build_tasks_tab(self):
        tasks_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["tasks"] = tasks_frame
        tasks_frame.grid_columnconfigure(0, weight=1)
        tasks_frame.grid_rowconfigure(1, weight=1)
        
        # Header Area
        header_frame = ctk.CTkFrame(tasks_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=12, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="📥 正在下載的任務", font=ctk.CTkFont(family="Segoe UI Variable Text", size=16, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        # Tasks Scrollable Area
        self.tasks_scroll_frame = ctk.CTkScrollableFrame(
            tasks_frame, 
            fg_color=self.c_frame, 
            border_color=self.c_card_border, 
            border_width=1, 
            corner_radius=12
        )
        self.tasks_scroll_frame.grid(row=1, column=0, padx=12, pady=5, sticky="nsew")
        self.tasks_scroll_frame.grid_columnconfigure(0, weight=1)
        
        self.task_ui_elements = {} # uid -> widgets dict

    def refresh_tasks_ui(self):
        # Filter to only include active downloads/recordings
        active_download_tasks = {}
        for uid, task in self.active_tasks.items():
            status = task.get("status", "")
            if status != "監控中":
                active_download_tasks[uid] = task
                
        active_count = len(active_download_tasks)
        
        # Update sidebar count badge
        if active_count > 0:
            self.nav_buttons["tasks"].configure(text=f"📥  下載任務  ({active_count})")
        else:
            self.nav_buttons["tasks"].configure(text="📥  下載任務")
            
        # 1. Remove finished tasks from UI
        active_uids = set(active_download_tasks.keys())
        for uid in list(self.task_ui_elements.keys()):
            if uid not in active_uids:
                for widget in self.task_ui_elements[uid]["widgets"]:
                    widget.destroy()
                self.task_ui_elements.pop(uid)
                
        # 2. Check if no tasks are running
        if not active_download_tasks:
            # Clear scrollable frame children if not already cleared
            if not self.tasks_scroll_frame.winfo_children():
                lbl = ctk.CTkLabel(self.tasks_scroll_frame, text="目前沒有進行中的下載任務", text_color=self.c_text_muted, font=ctk.CTkFont(family="Segoe UI Variable Text", size=13))
                lbl.pack(pady=40)
            return
            
        # Clear any empty text labels if tasks exist
        for child in self.tasks_scroll_frame.winfo_children():
            if isinstance(child, ctk.CTkLabel) and child.cget("text") == "目前沒有進行中的下載任務":
                child.destroy()
            
        # 3. Build UI row cards
        for uid, task in active_download_tasks.items():
            if uid not in self.task_ui_elements:
                row_frame = ctk.CTkFrame(
                    self.tasks_scroll_frame,
                    fg_color=self.c_card,
                    border_color=self.c_card_border,
                    border_width=1,
                    corner_radius=8
                )
                row_frame.pack(fill="x", padx=4, pady=4)
                row_frame.grid_columnconfigure(2, weight=1)
                
                # Platform outline badge
                plat = task.get("platform", "Unknown")
                p_color, p_bg = self.platform_colors.get(plat, (self.c_text_secondary, self.c_card))
                plat_lbl = ctk.CTkLabel(
                    row_frame, 
                    text=plat.upper(), 
                    fg_color=p_bg,
                    text_color=p_color, 
                    corner_radius=6,
                    width=75,
                    height=22,
                    font=ctk.CTkFont(family="Segoe UI Variable Text", size=9, weight="bold")
                )
                plat_lbl.grid(row=0, column=0, padx=12, pady=12)
                
                # Task Channel Name
                name_lbl = ctk.CTkLabel(row_frame, text=task.get("channel_name", "Unknown"), font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"), text_color=self.c_text_primary)
                name_lbl.grid(row=0, column=1, padx=8, pady=12, sticky="w")
                
                # Stats / Speed Label
                stats_lbl = ctk.CTkLabel(row_frame, text="佇列中...", text_color=self.c_text_secondary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=11))
                stats_lbl.grid(row=0, column=2, padx=12, pady=12, sticky="e")
                
                # Progress Bar
                prog_bar = ctk.CTkProgressBar(row_frame, width=190, height=8, corner_radius=4, progress_color=self.c_accent, fg_color=self.c_sidebar)
                prog_bar.grid(row=0, column=3, padx=12, pady=12)
                prog_bar.set(0)
                
                # Kill Button
                kill_btn = ctk.CTkButton(
                    row_frame, 
                    text="✖ 停止", 
                    width=65, 
                    height=28,
                    corner_radius=6,
                    fg_color=self.c_red, 
                    hover_color="#e11d48", 
                    font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
                    command=lambda u=uid: self.kill_active_task(u)
                )
                kill_btn.grid(row=0, column=4, padx=12, pady=12)
                
                self.task_ui_elements[uid] = {
                    "widgets": [row_frame, plat_lbl, name_lbl, stats_lbl, prog_bar, kill_btn],
                    "stats_lbl": stats_lbl,
                    "prog_bar": prog_bar
                }
                
            # Update stats dynamically
            elements = self.task_ui_elements[uid]
            status = task.get("status", "下載中")
            prog = task.get("progress", 0.0)
            speed = task.get("speed", "")
            size = task.get("size", "")
            elapsed = task.get("elapsed", "00:00")
            
            # Label wording
            stat_text = f"狀態: {status}"
            if elapsed:
                stat_text += f" | 已耗時: {elapsed}"
            if size:
                stat_text += f" | 大小: {size}"
            if speed:
                stat_text += f" | 速度: {speed}"
                
            elements["stats_lbl"].configure(text=stat_text)
            
            # Progress bar configuration
            if prog < 0:
                # Indeterminate status
                elements["prog_bar"].configure(mode="indeterminate")
                elements["prog_bar"].start()
            else:
                elements["prog_bar"].configure(mode="determinate")
                elements["prog_bar"].stop()
                elements["prog_bar"].set(prog)

    def kill_active_task(self, uid):
        task = self.active_tasks.get(uid)
        if task:
            proc = task.get("process")
            if proc:
                try:
                    from utils import kill_process_tree
                    kill_process_tree(proc)
                    self.add_log(f"已手動強制終止任務: {task['channel_name']}", "WARNING")
                except Exception as e:
                    self.add_log(f"終止任務子程序出錯: {e}", "ERROR")
            self.active_tasks.pop(uid, None)
            self.refresh_tasks_ui()

    # ================= History Tab =================
    def build_history_tab(self):
        hist_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["history"] = hist_frame
        hist_frame.grid_columnconfigure(0, weight=1)
        hist_frame.grid_rowconfigure(1, weight=1)
        
        # Header
        header_frame = ctk.CTkFrame(hist_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=12, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="📜 已完成的歷史紀錄 (近500筆)", font=ctk.CTkFont(family="Segoe UI Variable Text", size=16, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        self.btn_clear_history = ctk.CTkButton(
            header_frame, 
            text="🗑️ 清除歷史紀錄", 
            width=110, 
            height=30, 
            fg_color=self.c_red, 
            hover_color="#e11d48", 
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            corner_radius=6,
            command=self.clear_history
        )
        self.btn_clear_history.pack(side="right", padx=5)
        
        # Scroll Area
        self.hist_scroll_frame = ctk.CTkScrollableFrame(
            hist_frame, 
            fg_color=self.c_frame, 
            border_color=self.c_card_border, 
            border_width=1, 
            corner_radius=12
        )
        self.hist_scroll_frame.grid(row=1, column=0, padx=12, pady=5, sticky="nsew")
        self.hist_scroll_frame.grid_columnconfigure(2, weight=1)

    def clear_history(self):
        if not self.history:
            messagebox.showinfo("提示", "目前沒有下載歷史紀錄可以清除。")
            return
            
        ans = messagebox.askyesno("確認清除", "確定要清除所有的下載歷史紀錄嗎？\n(這不會刪除任何已下載的影片檔案)")
        if ans:
            if database.clear_history(self):
                self.add_log("已成功清除下載歷史紀錄", "SUCCESS")

    def refresh_history_ui(self):
        for widget in self.hist_scroll_frame.winfo_children():
            widget.destroy()
            
        if not self.history:
            ctk.CTkLabel(self.hist_scroll_frame, text="尚無已完成的下載歷史紀錄", text_color=self.c_text_muted, font=ctk.CTkFont(family="Segoe UI Variable Text", size=13)).pack(pady=40)
            return
            
        for entry in self.history:
            row_frame = ctk.CTkFrame(
                self.hist_scroll_frame,
                fg_color=self.c_card,
                border_color=self.c_card_border,
                border_width=1,
                corner_radius=8
            )
            row_frame.pack(fill="x", padx=4, pady=4)
            row_frame.grid_columnconfigure(2, weight=1)
            
            # Platform Badge
            plat = entry.get("platform", "Unknown")
            p_color, p_bg = self.platform_colors.get(plat, (self.c_text_secondary, self.c_card))
            plat_badge = ctk.CTkLabel(
                row_frame, 
                text=plat.upper(), 
                fg_color=p_bg,
                text_color=p_color, 
                corner_radius=6,
                width=75,
                height=22,
                font=ctk.CTkFont(family="Segoe UI Variable Text", size=9, weight="bold")
            )
            plat_badge.grid(row=0, column=0, padx=12, pady=10)
            
            # Channel Details Stack
            left_info_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            left_info_frame.grid(row=0, column=1, padx=5, pady=5, sticky="w")
            
            chan_lbl = ctk.CTkLabel(left_info_frame, text=entry.get("channel", "Unknown"), font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"), text_color=self.c_text_primary)
            chan_lbl.pack(anchor="w")
            time_lbl = ctk.CTkLabel(left_info_frame, text=entry.get("timestamp", ""), font=ctk.CTkFont(family="Segoe UI Variable Text", size=10), text_color=self.c_text_muted)
            time_lbl.pack(anchor="w")
            
            # Video Title
            v_title = entry.get("title", "未命名標題")
            if len(v_title) > 60:
                v_title = v_title[:57] + "..."
            title_lbl = ctk.CTkLabel(row_frame, text=v_title, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12), text_color=self.c_text_primary)
            title_lbl.grid(row=0, column=2, padx=12, pady=10, sticky="w")
            
            # Size Label
            size_lbl = ctk.CTkLabel(row_frame, text=entry.get("size", "Unknown"), font=ctk.CTkFont(family="Segoe UI Variable Text", size=11), text_color=self.c_text_secondary)
            size_lbl.grid(row=0, column=3, padx=12, pady=10)
            
            # Quick Actions Frame
            act_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            act_frame.grid(row=0, column=4, padx=12, pady=10, sticky="e")
            
            path = entry.get("file_path", "")
            
            open_btn = ctk.CTkButton(
                act_frame, 
                text="📂 資料夾", 
                width=65, 
                height=26, 
                fg_color="#1e293b", 
                hover_color="#334155", 
                text_color=self.c_text_primary,
                corner_radius=6,
                font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
                command=lambda p=path: self.open_containing_folder(p)
            )
            open_btn.pack(side="left", padx=4)
            
            play_btn = ctk.CTkButton(
                act_frame, 
                text="▶️ 播放", 
                width=55, 
                height=26, 
                fg_color=self.c_accent, 
                hover_color=self.c_accent_hover,
                text_color=self.c_text_primary,
                corner_radius=6,
                font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
                command=lambda p=path: self.play_video(p)
            )
            play_btn.pack(side="left", padx=4)

    def open_containing_folder(self, file_path):
        if not file_path:
            return
        # If absolute folder doesn't exist, try local relative pathing
        if not os.path.exists(file_path):
            file_path = os.path.join(BASE_DIR, file_path)
        if os.path.exists(file_path):
            try:
                subprocess.Popen(f'explorer /select,"{file_path}"')
            except Exception as e:
                self.add_log(f"無法打開資料夾: {e}", "ERROR")
        else:
            self.add_log("找不到對應的檔案或目錄！", "WARNING")

    def play_video(self, file_path):
        if not file_path:
            return
        if not os.path.exists(file_path):
            file_path = os.path.join(BASE_DIR, file_path)
        if os.path.exists(file_path):
            try:
                os.startfile(file_path)
            except Exception as e:
                self.add_log(f"無法播放影片: {e}", "ERROR")
        else:
            self.add_log("影片檔案不存在或已被移動！", "WARNING")

    # ================= Manual Download Tab =================
    def build_manual_tab(self):
        manual_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["manual"] = manual_frame
        manual_frame.grid_columnconfigure(0, weight=1)
        manual_frame.grid_rowconfigure(1, weight=1)
        
        # Header
        header_frame = ctk.CTkFrame(manual_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=12, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="🔗 指派單次影音手動下載任務", font=ctk.CTkFont(family="Segoe UI Variable Text", size=16, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        # Content frame
        content_box = ctk.CTkFrame(manual_frame, fg_color=self.c_frame, border_color=self.c_card_border, border_width=1, corner_radius=12)
        content_box.grid(row=1, column=0, padx=12, pady=5, sticky="nsew")
        content_box.grid_columnconfigure(1, weight=1)
        content_box.grid_rowconfigure(5, weight=1)
        
        form_label_font = ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold")
        
        # URL Field
        ctk.CTkLabel(content_box, text="影音網址 (URL):", text_color=self.c_text_secondary, font=form_label_font).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")
        
        url_container = ctk.CTkFrame(content_box, fg_color="transparent")
        url_container.grid(row=0, column=1, columnspan=2, padx=20, pady=(20, 8), sticky="ew")
        url_container.grid_columnconfigure(0, weight=1)
        
        self.manual_url_entry = ctk.CTkEntry(
            url_container,
            placeholder_text="輸入單個直播、影片、YouTube 網址...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            height=34,
            corner_radius=8,
            text_color=self.c_text_primary,
            placeholder_text_color=self.c_text_muted
        )
        self.manual_url_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        
        load_txt_btn = ctk.CTkButton(
            url_container,
            text="📁 載入 TXT...",
            width=95,
            height=34,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color=self.c_text_primary,
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            command=self.load_manual_urls_from_txt
        )
        load_txt_btn.grid(row=0, column=1, padx=0, pady=0)
        
        # Custom Subfolder Field
        ctk.CTkLabel(content_box, text="儲存資料夾名稱/路徑:", text_color=self.c_text_secondary, font=form_label_font).grid(row=1, column=0, padx=20, pady=8, sticky="w")
        
        subfolder_container = ctk.CTkFrame(content_box, fg_color="transparent")
        subfolder_container.grid(row=1, column=1, columnspan=2, padx=20, pady=8, sticky="ew")
        subfolder_container.grid_columnconfigure(0, weight=1)
        
        self.manual_name_entry = ctk.CTkEntry(
            subfolder_container,
            placeholder_text="例如: セラ (預設儲存資料夾名稱)，或瀏覽選取實體路徑...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            height=34,
            corner_radius=8,
            text_color=self.c_text_primary,
            placeholder_text_color=self.c_text_muted
        )
        self.manual_name_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        
        browse_dir_btn = ctk.CTkButton(
            subfolder_container, 
            text="瀏覽...", 
            width=80, 
            height=34,
            fg_color="#1e293b", 
            hover_color="#334155", 
            text_color=self.c_text_primary,
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            command=self.browse_manual_download_directory
        )
        browse_dir_btn.grid(row=0, column=1, padx=0, pady=0)
        
        # Platform Selection Dropdown
        ctk.CTkLabel(content_box, text="指定平台:", text_color=self.c_text_secondary, font=form_label_font).grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.manual_platform_var = ctk.StringVar(value="自動偵測")
        self.manual_plat_menu = ctk.CTkOptionMenu(
            content_box,
            variable=self.manual_platform_var,
            values=["自動偵測", "Rplay", "YouTube", "Other (yt-dlp)"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            height=32,
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12)
        )
        self.manual_plat_menu.grid(row=2, column=1, padx=20, pady=8, sticky="w")
        
        # Quality & Format Selection for Manual Download
        ctk.CTkLabel(content_box, text="畫質與格式選擇:", text_color=self.c_text_secondary, font=form_label_font).grid(row=3, column=0, padx=20, pady=8, sticky="w")
        
        qf_container = ctk.CTkFrame(content_box, fg_color="transparent")
        qf_container.grid(row=3, column=1, columnspan=2, padx=20, pady=8, sticky="ew")
        
        # Quality dropdown
        self.manual_quality_var = ctk.StringVar(value="best")
        self.manual_quality_menu = ctk.CTkOptionMenu(
            qf_container,
            variable=self.manual_quality_var,
            values=["best", "1080p", "720p", "480p", "360p", "worst"],
            width=100,
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12)
        )
        self.manual_quality_menu.pack(side="left", padx=(0, 15))
        
        ctk.CTkLabel(qf_container, text="格式:", text_color=self.c_text_secondary, font=form_label_font).pack(side="left", padx=(0, 5))
        
        # Format dropdown
        self.manual_format_var = ctk.StringVar(value="mp4")
        self.manual_format_menu = ctk.CTkOptionMenu(
            qf_container,
            variable=self.manual_format_var,
            values=["mp4", "mkv", "webm", "ts"],
            width=100,
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12)
        )
        self.manual_format_menu.pack(side="left")
        
        # Trigger Actions Frame
        self.manual_dl_btn = ctk.CTkButton(
            content_box,
            text="📥 指派並開始下載任務",
            height=42,
            fg_color=self.c_green,
            hover_color="#059669",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=13, weight="bold"),
            corner_radius=8,
            command=self.trigger_manual_download
        )
        self.manual_dl_btn.grid(row=4, column=0, columnspan=3, padx=20, pady=20, sticky="ew")

    def trigger_manual_download(self):
        raw_input = self.manual_url_entry.get().strip()
        if not raw_input:
            self.add_log("手動下載失敗: 影音網址或 TXT 檔案路徑不能為空！", "WARNING")
            return
            
        custom_path = self.manual_name_entry.get().strip()
        if not custom_path:
            custom_path = "Manual_Downloads"
            
        quality = self.manual_quality_var.get()
        fmt = self.manual_format_var.get()
        
        # Check if raw_input is a TXT file (either existing file or ends with .txt/.list)
        is_txt_file = False
        if os.path.isfile(raw_input) and not raw_input.startswith(('http://', 'https://')):
            is_txt_file = True
        elif raw_input.lower().endswith(('.txt', '.list')) and not raw_input.startswith(('http://', 'https://')):
            is_txt_file = True
            
        if is_txt_file:
            if not os.path.isfile(raw_input):
                self.add_log(f"找不到 TXT 檔案: {raw_input}", "ERROR")
                messagebox.showerror("錯誤", f"找不到 TXT 檔案：\n{raw_input}", parent=self)
                return
                
            line_count = 0
            first_url = ""
            try:
                with open(raw_input, 'rb') as f:
                    raw_bytes = f.read()
                content = ""
                if raw_bytes.startswith(b'\xff\xfe') or raw_bytes.startswith(b'\xfe\xff'):
                    try: content = raw_bytes.decode('utf-16')
                    except: pass
                elif raw_bytes.startswith(b'\xef\xbb\xbf'):
                    try: content = raw_bytes.decode('utf-8-sig')
                    except: pass
                if not content:
                    for enc in ('utf-8', 'utf-8-sig', 'cp950', 'big5', 'gb18030', 'utf-16', 'latin1'):
                        try:
                            content = raw_bytes.decode(enc)
                            break
                        except:
                            continue
                if not content:
                    content = raw_bytes.decode('utf-8', errors='ignore')
                    
                for raw_l in content.splitlines():
                    l = raw_l.strip()
                    if l and not l.startswith(('#', '//')):
                        line_count += 1
                        if not first_url and ('http' in l or 'rplay' in l or 'youtube' in l or 'withny' in l or 'fc2' in l):
                            first_url = l
            except Exception as e:
                self.add_log(f"讀取 TXT 檔案失敗: {e}", "ERROR")
                messagebox.showerror("錯誤", f"讀取 TXT 檔案失敗: {e}", parent=self)
                return
                
            plat = self.manual_platform_var.get()
            if plat == "自動偵測":
                plat = detect_platform(first_url or raw_input)
                
            display_name = os.path.basename(custom_path) if (":" in custom_path or "/" in custom_path or "\\" in custom_path) else custom_path
            if not display_name:
                display_name = os.path.basename(raw_input)
                
            uid = f"batch_{int(time.time())}_{random.randint(1000, 9999)}"
            self.active_tasks[uid] = {
                "channel_name": f"[批量排程] {display_name} ({line_count} 個項目)",
                "platform": plat,
                "url": raw_input,
                "status": "排程下載中",
                "progress": 0.0,
                "speed": "",
                "size": "",
                "elapsed": "00:00",
                "start_time": time.time(),
                "process": None
            }
            self.refresh_tasks_ui()
            self.select_tab("tasks")
            self.add_log(f"已指派 TXT 批量排程任務: {raw_input} (共 {line_count} 個項目，由 yt-dlp 逐一下載)", "INFO")
            
            t = threading.Thread(target=workers.worker_manual_download, args=(self, uid, raw_input, custom_path, plat, quality, fmt), daemon=True)
            t.start()
            return
            
        # Single URL Workflow
        url = smart_redirect_url(raw_input)
        self.manual_url_entry.delete(0, 'end')
        self.manual_url_entry.insert(0, url)
        
        plat = self.manual_platform_var.get()
        if plat == "自動偵測":
            plat = detect_platform(url)
            
        display_name = os.path.basename(custom_path) if (":" in custom_path or "/" in custom_path or "\\" in custom_path) else custom_path
        if not display_name:
            display_name = "Manual_Download"
            
        uid = f"manual_{int(time.time())}_{random.randint(1000, 9999)}"
        self.active_tasks[uid] = {
            "channel_name": f"[手動] {display_name}",
            "platform": plat,
            "url": url,
            "status": "開始下載",
            "progress": 0.0,
            "speed": "",
            "size": "",
            "elapsed": "00:00",
            "start_time": time.time(),
            "process": None
        }
        
        self.add_log(f"已手動指派下載任務: {url}", "INFO")
        self.select_tab("tasks")
        
        t = threading.Thread(target=workers.worker_manual_download, args=(self, uid, url, custom_path, plat, quality, fmt), daemon=True)
        t.start()

    def resume_paused_rplay_task(self, task_info):
        if not task_info:
            return
        url = task_info.get("url")
        custom_path = task_info.get("custom_path", "")
        plat = task_info.get("platform", "Rplay")
        quality = task_info.get("quality", "best")
        fmt = task_info.get("fmt", "best")
        display_name = task_info.get("display_name", "Rplay_Task")
        is_batch = task_info.get("is_batch", False)
        
        uid = f"resume_{int(time.time())}_{random.randint(1000, 9999)}"
        self.active_tasks[uid] = {
            "channel_name": f"[重派排程] {display_name}" if is_batch else f"[重派手動] {display_name}",
            "platform": plat,
            "url": url,
            "status": "排程下載中" if is_batch else "下載中",
            "progress": 0.0,
            "speed": "",
            "size": "",
            "elapsed": "00:00",
            "start_time": time.time(),
            "process": None
        }
        self.refresh_tasks_ui()
        self.add_log(f"🚀 [自動派回] 已確認 Token 認證有效，重新啟動下載任務: {display_name}", "SUCCESS")
        t = threading.Thread(target=workers.worker_manual_download, args=(self, uid, url, custom_path, plat, quality, fmt), daemon=True)
        t.start()

    # ================= Settings Tab =================
    def build_settings_tab(self):
        settings_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["settings"] = settings_frame
        settings_frame.grid_columnconfigure(0, weight=1)
        settings_frame.grid_rowconfigure(1, weight=1)
        
        # Header Area
        header_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=12, pady=(10, 15), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(header_frame, text="⚙️ 系統及平台設定", font=ctk.CTkFont(family="Segoe UI Variable Text", size=16, weight="bold"), text_color=self.c_text_primary).grid(row=0, column=0, sticky="w")
        
        self.save_settings_btn = ctk.CTkButton(
            header_frame, 
            text="💾 儲存所有設定", 
            fg_color=self.c_green, 
            hover_color="#059669", 
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            height=32,
            corner_radius=6,
            command=self.apply_and_save_settings_gui
        )
        self.save_settings_btn.grid(row=0, column=1, sticky="e")
        
        # Settings Fields Scrollable Frame
        scroll_settings = ctk.CTkScrollableFrame(settings_frame, fg_color=self.c_frame, border_color=self.c_card_border, border_width=1, corner_radius=12)
        scroll_settings.grid(row=1, column=0, padx=12, pady=5, sticky="nsew")
        scroll_settings.grid_columnconfigure(1, weight=1)
        
        form_label_font = ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold")
        header_font = ctk.CTkFont(family="Segoe UI Variable Text", size=14, weight="bold")
        
        row_idx = 0
        
        # ================= 🖥️ 系統運作設定 =================
        ctk.CTkLabel(scroll_settings, text="🖥️ 系統運作設定", font=header_font, text_color=self.c_text_primary).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=(16, 10), sticky="w")
        row_idx += 1
        
        # 預設檔案儲存路徑
        ctk.CTkLabel(scroll_settings, text="預設檔案儲存路徑:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        dir_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        dir_frame.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        dir_frame.grid_columnconfigure(0, weight=1)
        
        self.download_dir_entry = ctk.CTkEntry(dir_frame, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.download_dir_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.download_dir_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        dir_browse_btn = ctk.CTkButton(dir_frame, text="瀏覽...", width=75, height=34, fg_color="#1e293b", hover_color="#334155", text_color=self.c_text_primary, corner_radius=6, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"), command=self.browse_download_directory)
        dir_browse_btn.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # Discord Bot Token
        ctk.CTkLabel(scroll_settings, text="Discord Bot Token:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.discord_bot_token_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.discord_bot_token_entry.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        self.discord_bot_token_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1

        # Discord 頻道 ID
        ctk.CTkLabel(scroll_settings, text="Discord 直播狀態頻道 ID:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.discord_channel_id_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.discord_channel_id_entry.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        self.discord_channel_id_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # Discord 下載完成頻道 ID
        ctk.CTkLabel(scroll_settings, text="Discord 下載完成頻道 ID:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.discord_completed_channel_id_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.discord_completed_channel_id_entry.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        self.discord_completed_channel_id_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # 手動排程下載上限
        ctk.CTkLabel(scroll_settings, text="手動排程下載上限:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.max_dl_spinner = ctk.CTkEntry(scroll_settings, width=80, height=34, corner_radius=6, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.max_dl_spinner.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        self.max_dl_spinner.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # yt-dlp 多通路分段併發下載數 (-N)
        ctk.CTkLabel(scroll_settings, text="yt-dlp 多通路分段加速 (-N):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.concurrent_frags_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["8 通路 (推薦高速)", "16 通路 (極速)", "32 通路 (超速)", "4 通路 (標準)", "1 通路 (單線程)"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.concurrent_frags_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # 自訂連線 User-Agent
        ctk.CTkLabel(scroll_settings, text="自訂連線 User-Agent:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.ua_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.ua_entry.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        self.ua_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # 下載防睡眠設定
        ctk.CTkLabel(scroll_settings, text="下載防睡眠設定:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.prevent_sleep_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["下載/監控時阻擋睡眠", "完全阻擋睡眠", "恢復系統預設"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.prevent_sleep_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=1, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=12, sticky="ew")
        row_idx += 1
        
        # ================= 🎥 YouTube 下載設定 =================
        ctk.CTkLabel(scroll_settings, text="🎥 YouTube 下載設定", font=header_font, text_color=self.c_red_text).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=(12, 8), sticky="w")
        row_idx += 1
        
        # Cookies 檔案位置
        ctk.CTkLabel(scroll_settings, text="YouTube Cookies 檔案位置:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        cookies_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        cookies_frame.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        cookies_frame.grid_columnconfigure(0, weight=1)
        
        self.cookies_file_entry = ctk.CTkEntry(cookies_frame, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.cookies_file_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.cookies_file_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        cookies_browse_btn = ctk.CTkButton(cookies_frame, text="瀏覽...", width=75, height=34, fg_color="#1e293b", hover_color="#334155", text_color=self.c_text_primary, corner_radius=6, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"), command=self.browse_cookies_file)
        cookies_browse_btn.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # YT 畫質優先度 (yt-dlp)
        ctk.CTkLabel(scroll_settings, text="YT 畫質優先度 (yt-dlp):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.yt_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["best", "1080p", "720p", "480p", "360p", "worst"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.yt_quality_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # YT 檔案格式 (yt-dlp)
        ctk.CTkLabel(scroll_settings, text="YT 檔案格式 (yt-dlp):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.yt_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "webm"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.yt_format_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # 指定關鍵字
        ctk.CTkLabel(scroll_settings, text="指定關鍵字:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.yt_keywords_entry = ctk.CTkEntry(
            scroll_settings, 
            placeholder_text="例如: 歌枠,歌回 (標題包含任一詞才下載，空白則全下載)",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            height=34,
            corner_radius=6,
            text_color=self.c_text_primary,
            placeholder_text_color=self.c_text_muted
        )
        self.yt_keywords_entry.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        self.yt_keywords_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=1, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=12, sticky="ew")
        row_idx += 1
        
        # ================= 🎵 Rplay 平台設定 =================
        ctk.CTkLabel(scroll_settings, text="🎵 Rplay 平台設定", font=header_font, text_color=self.c_blue_text).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=(12, 8), sticky="w")
        row_idx += 1
        
        # Rplay Token
        ctk.CTkLabel(scroll_settings, text="Rplay Token:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        rplay_pwd_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        rplay_pwd_frame.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        rplay_pwd_frame.grid_columnconfigure(0, weight=1)
        
        self.rplay_token_entry = ctk.CTkEntry(rplay_pwd_frame, show="*", fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.rplay_token_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.rplay_token_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        self.btn_toggle_rplay_token = ctk.CTkButton(
            rplay_pwd_frame, text="👁️", width=36, height=34, fg_color="#1e293b", hover_color="#334155", text_color=self.c_text_primary, corner_radius=6,
            command=lambda: self.toggle_password_visibility(self.rplay_token_entry, self.btn_toggle_rplay_token)
        )
        self.btn_toggle_rplay_token.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1

        # Rplay User OID (24碼)
        ctk.CTkLabel(scroll_settings, text="Rplay User OID (24碼):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.rplay_username_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6, placeholder_text="例如: 660d6cee4eb65b83664f365b", placeholder_text_color=self.c_text_muted)
        self.rplay_username_entry.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        self.rplay_username_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # 油猴 / 跨裝置 Token 同步位址一鍵複製
        ctk.CTkLabel(scroll_settings, text="Token 同步位址複製:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        sync_urls_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        sync_urls_frame.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        
        btn_copy_local_sync = ctk.CTkButton(
            sync_urls_frame, 
            text="💻 複製本機同步位址 (127.0.0.1)", 
            fg_color="#1e293b", 
            hover_color="#334155", 
            height=32,
            corner_radius=6,
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
            command=self.copy_local_sync_url
        )
        btn_copy_local_sync.pack(side="left", padx=(0, 10))
        
        btn_copy_wifi_sync = ctk.CTkButton(
            sync_urls_frame, 
            text="📱 複製 Wi-Fi 跨裝置同步位址 (動態 IP)", 
            fg_color=self.c_accent, 
            hover_color=self.c_accent_hover, 
            height=32,
            corner_radius=6,
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
            command=self.copy_wifi_sync_url
        )
        btn_copy_wifi_sync.pack(side="left")
        row_idx += 1
        
        # Rplay 畫質優先度
        ctk.CTkLabel(scroll_settings, text="Rplay 畫質優先度:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.rplay_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["best", "1080p", "720p", "480p", "360p", "worst"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.rplay_quality_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1

        # Rplay 檔案格式
        ctk.CTkLabel(scroll_settings, text="Rplay 檔案格式:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.rplay_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "ts"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.rplay_format_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=1, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=12, sticky="ew")
        row_idx += 1
        
        # ================= 🪐 Withny 平台設定 =================
        ctk.CTkLabel(scroll_settings, text="🪐 Withny 平台設定", font=header_font, text_color=self.c_purple_text).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=(12, 8), sticky="w")
        row_idx += 1
        
        # Withny Token
        ctk.CTkLabel(scroll_settings, text="Withny Token (Session):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        withny_pwd_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        withny_pwd_frame.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        withny_pwd_frame.grid_columnconfigure(0, weight=1)
        
        self.withny_token_entry = ctk.CTkEntry(withny_pwd_frame, show="*", fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.withny_token_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.withny_token_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        self.btn_toggle_withny_token = ctk.CTkButton(
            withny_pwd_frame, text="👁️", width=36, height=34, fg_color="#1e293b", hover_color="#334155", text_color=self.c_text_primary, corner_radius=6,
            command=lambda: self.toggle_password_visibility(self.withny_token_entry, self.btn_toggle_withny_token)
        )
        self.btn_toggle_withny_token.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # Withny Token 同步位址複製
        ctk.CTkLabel(scroll_settings, text="Token 同步位址複製:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        withny_sync_urls_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        withny_sync_urls_frame.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        
        btn_copy_withny_local = ctk.CTkButton(
            withny_sync_urls_frame, 
            text="💻 複製本機同步位址 (127.0.0.1)", 
            fg_color="#1e293b", 
            hover_color="#334155", 
            height=32,
            corner_radius=6,
            text_color=self.c_text_primary,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
            command=self.copy_withny_local_sync_url
        )
        btn_copy_withny_local.pack(side="left", padx=(0, 10))
        
        btn_copy_withny_wifi = ctk.CTkButton(
            withny_sync_urls_frame, 
            text="📱 複製 Wi-Fi 跨裝置同步位址 (動態 IP)", 
            fg_color=self.c_purple, 
            hover_color=self.c_accent_hover, 
            height=32,
            corner_radius=6,
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
            command=self.copy_withny_wifi_sync_url
        )
        btn_copy_withny_wifi.pack(side="left")
        row_idx += 1
        
        # Withny 畫質優先度 (僅限原始畫質)
        ctk.CTkLabel(scroll_settings, text="Withny 畫質優先度:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.withny_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["自動(原始)"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color="#334155",
            button_hover_color="#334155",
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            state="disabled"
        )
        self.withny_quality_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Withny 影音格式轉換 (Remux)
        ctk.CTkLabel(scroll_settings, text="Withny 影音格式轉換 (Remux):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.withny_remux_var = ctk.BooleanVar()
        self.withny_remux_switch = ctk.CTkSwitch(
            scroll_settings, text="啟用即時重封裝 (不推薦，建議使用 Concat 代替)", variable=self.withny_remux_var,
            text_color=self.c_text_primary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12), progress_color=self.c_accent, button_color=self.c_text_primary, button_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.withny_remux_switch.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Withny Remux 目標副檔名
        ctk.CTkLabel(scroll_settings, text="Withny Remux 目標副檔名:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.withny_remux_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "mov", "ts"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.withny_remux_format_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Withny 合併連續分段 (Concat)
        ctk.CTkLabel(scroll_settings, text="Withny 合併連續分段 (Concat):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.withny_concat_var = ctk.BooleanVar()
        self.withny_concat_switch = ctk.CTkSwitch(
            scroll_settings, text="自動合併並重封裝為目標格式 (推薦，能防止損壞)", variable=self.withny_concat_var,
            text_color=self.c_text_primary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12), progress_color=self.c_accent, button_color=self.c_text_primary, button_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.withny_concat_switch.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Withny 保留 TS 暫存分段
        ctk.CTkLabel(scroll_settings, text="Withny 保留 TS 暫存分段:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.withny_keep_var = ctk.BooleanVar()
        self.withny_keep_switch = ctk.CTkSwitch(
            scroll_settings, text="合併後保留原本的分段小 TS 檔案", variable=self.withny_keep_var,
            text_color=self.c_text_primary, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12), progress_color=self.c_accent, button_color=self.c_text_primary, button_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.withny_keep_switch.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Withny 斷線重試檢查間隔
        ctk.CTkLabel(scroll_settings, text="Withny 斷線重試檢查間隔:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.withny_wait_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.withny_wait_entry.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        self.withny_wait_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=1, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=12, sticky="ew")
        row_idx += 1
        
        # ================= 📺 FC2 平台設定 =================
        ctk.CTkLabel(scroll_settings, text="📺 FC2 平台設定", font=header_font, text_color=self.c_yellow_text).grid(row=row_idx, column=0, columnspan=2, padx=16, pady=(12, 8), sticky="w")
        row_idx += 1
        
        # FC2 Cookies 檔案位置
        ctk.CTkLabel(scroll_settings, text="FC2 Cookies 檔案位置:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        fc2_cookies_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        fc2_cookies_frame.grid(row=row_idx, column=1, padx=20, pady=6, sticky="ew")
        fc2_cookies_frame.grid_columnconfigure(0, weight=1)
        
        self.fc2_cookies_file_entry = ctk.CTkEntry(fc2_cookies_frame, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary, height=34, corner_radius=6)
        self.fc2_cookies_file_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.fc2_cookies_file_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        fc2_cookies_browse_btn = ctk.CTkButton(fc2_cookies_frame, text="瀏覽...", width=75, height=34, fg_color="#1e293b", hover_color="#334155", text_color=self.c_text_primary, corner_radius=6, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"), command=self.browse_fc2_cookies_file)
        fc2_cookies_browse_btn.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # FC2 畫質優先度
        ctk.CTkLabel(scroll_settings, text="FC2 畫質優先度:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.fc2_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["3Mbps", "2Mbps", "1.2Mbps", "400Kbps", "150Kbps", "sound"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.fc2_quality_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1

        # FC2 檔案格式
        ctk.CTkLabel(scroll_settings, text="FC2 檔案格式:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=20, pady=6, sticky="w")
        self.fc2_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "ts"],
            height=32,
            corner_radius=6,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color="#1e293b",
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12),
            command=self.on_settings_modified
        )
        self.fc2_format_menu.grid(row=row_idx, column=1, padx=20, pady=6, sticky="w")
        row_idx += 1
        
        # Load form fields
        self.reset_settings_fields_from_state()
        self.settings_dirty = False

    def toggle_password_visibility(self, entry, button):
        if entry.cget("show") == "*":
            entry.configure(show="")
            button.configure(text="🔒")
        else:
            entry.configure(show="*")
            button.configure(text="👁️")

    def browse_download_directory(self):
        d = ctk.filedialog.askdirectory(initialdir=self.settings["download_dir"])
        if d:
            self.download_dir_entry.delete(0, "end")
            self.download_dir_entry.insert(0, d.replace("\\", "/"))
            self.on_settings_modified()

    def browse_cookies_file(self):
        f = ctk.filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if f:
            self.cookies_file_entry.delete(0, "end")
            self.cookies_file_entry.insert(0, f.replace("\\", "/"))
            self.on_settings_modified()

    def browse_fc2_cookies_file(self):
        f = ctk.filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if f:
            self.fc2_cookies_file_entry.delete(0, "end")
            self.fc2_cookies_file_entry.insert(0, f.replace("\\", "/"))
            self.on_settings_modified()

    def browse_manual_download_directory(self):
        d = ctk.filedialog.askdirectory(initialdir=self.settings["download_dir"])
        if d:
            self.manual_name_entry.delete(0, "end")
            self.manual_name_entry.insert(0, d.replace("\\", "/"))

    def load_manual_urls_from_txt(self):
        filepath = ctk.filedialog.askopenfilename(
            parent=self,
            title="選取包含網址的 TXT 檔案",
            filetypes=[("Text files", "*.txt"), ("List files", "*.list"), ("All files", "*.*")]
        )
        if not filepath:
            return
            
        clean_path = filepath.replace("\\", "/")
        self.manual_url_entry.delete(0, "end")
        self.manual_url_entry.insert(0, clean_path)
        self.add_log(f"已選取 TXT 網址清單檔案: {clean_path}", "INFO")

    def get_current_lan_ip(self):
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def copy_local_sync_url(self):
        url = "http://127.0.0.1:18730/update_token"
        self.clipboard_clear()
        self.clipboard_append(url)
        self.add_log(f"📋 已複製本機 Token 同步位址: {url}", "SUCCESS")
        messagebox.showinfo("📋 已複製位址", f"【本機專用】同步位址已複製到剪貼簿：\n\n{url}\n\n適用於電腦本機瀏覽器 (Chrome / Edge / Firefox) 油猴腳本。")

    def copy_wifi_sync_url(self):
        current_ip = self.get_current_lan_ip()
        url = f"http://{current_ip}:18730/update_token"
        self.clipboard_clear()
        self.clipboard_append(url)
        self.add_log(f"📱 已複製 Rplay 動態局域網 Token 同步位址: {url}", "SUCCESS")
        messagebox.showinfo("📋 已複製位址", f"【Rplay 跨裝置 Wi-Fi 專用】動態同步位址已複製到剪貼簿：\n\n{url}\n\n目前動態偵測本機 IP: {current_ip}\n適用於手機 Safari (Userscripts/Stay) 或其他連至同 Wi-Fi 之裝置。")

    def copy_withny_local_sync_url(self):
        url = "http://127.0.0.1:18730/update_withny_token"
        self.clipboard_clear()
        self.clipboard_append(url)
        self.add_log(f"📋 已複製 Withny 本機 Token 同步位址: {url}", "SUCCESS")
        messagebox.showinfo("📋 已複製位址", f"【Withny 本機專用】同步位址已複製到剪貼簿：\n\n{url}\n\n適用於電腦本機瀏覽器 (Chrome / Edge / Firefox) 油猴腳本。")

    def start_api_manual_download(self, url, custom_path="Manual_Downloads", plat="自動偵測", quality="best", fmt="mp4"):
        import random
        url = smart_redirect_url(url)
        if not url:
            return False, "影音網址不能為空"
            
        if not custom_path:
            custom_path = "Manual_Downloads"
            
        if plat == "自動偵測":
            plat = detect_platform(url)
            
        display_name = os.path.basename(custom_path) if (":" in custom_path or "/" in custom_path or "\\" in custom_path) else custom_path
        if not display_name:
            display_name = "Manual_Download"
            
        uid = f"manual_{int(time.time())}_{random.randint(1000, 9999)}"
        self.active_tasks[uid] = {
            "channel_name": f"[遠端手動] {display_name}",
            "platform": plat,
            "url": url,
            "status": "開始下載",
            "progress": 0.0,
            "speed": "",
            "size": "",
            "elapsed": "00:00",
            "start_time": time.time(),
            "process": None
        }
        
        self.add_log(f"🌐 [遠端控制] 手動指派下載任務: {url}", "INFO")
        try:
            self.gui_update_queue.put(("refresh_tasks", None))
        except Exception:
            pass
            
        t = threading.Thread(target=workers.worker_manual_download, args=(self, uid, url, custom_path, plat, quality, fmt), daemon=True)
        t.start()
        return True, "已成功啟動手動下載任務"

    def copy_withny_wifi_sync_url(self):
        current_ip = self.get_current_lan_ip()
        url = f"http://{current_ip}:18730/update_withny_token"
        self.clipboard_clear()
        self.clipboard_append(url)
        self.add_log(f"📱 已複製 Withny 動態局域網 Token 同步位址: {url}", "SUCCESS")
        messagebox.showinfo("📱 已複製位址", f"【Withny 跨裝置 Wi-Fi 專用】動態同步位址已複製到剪貼簿：\n\n{url}\n\n目前動態偵測本機 IP: {current_ip}\n適用於手機 Safari (Userscripts/Stay) 或其他連至同 Wi-Fi 之裝置。")

    def apply_and_save_settings_gui(self):
        self.settings["rplay_token"] = self.rplay_token_entry.get().strip()
        self.settings["rplay_username"] = self.rplay_username_entry.get().strip()
        self.settings["withny_token"] = self.withny_token_entry.get().strip()
        self.settings["download_dir"] = self.download_dir_entry.get().strip().replace("\\", "/")
        self.settings["cookies_file"] = self.cookies_file_entry.get().strip().replace("\\", "/")
        self.settings["fc2_cookies_file"] = self.fc2_cookies_file_entry.get().strip().replace("\\", "/")
        self.settings["discord_bot_token"] = self.discord_bot_token_entry.get().strip()
        self.settings["discord_channel_id"] = self.discord_channel_id_entry.get().strip()
        self.settings["discord_completed_channel_id"] = self.discord_completed_channel_id_entry.get().strip()
        self.settings["prevent_sleep_mode"] = self.prevent_sleep_menu.get()
        
        try:
            self.settings["max_concurrent_downloads"] = int(self.max_dl_spinner.get().strip())
        except:
            self.settings["max_concurrent_downloads"] = 2
            
        raw_cf = self.concurrent_frags_menu.get()
        try:
            self.settings["concurrent_fragments"] = int(raw_cf.split()[0])
        except:
            self.settings["concurrent_fragments"] = 8
            
        self.settings["yt_quality"] = self.yt_quality_menu.get()
        self.settings["yt_format"] = self.yt_format_menu.get()
        
        raw_kws = self.yt_keywords_entry.get().strip()
        if not raw_kws:
            self.settings["yt_keywords"] = []
        else:
            self.settings["yt_keywords"] = [k.strip() for k in raw_kws.replace("，", ",").split(",") if k.strip()]
            
        self.settings["rplay_quality"] = self.rplay_quality_menu.get()
        self.settings["rplay_format"] = self.rplay_format_menu.get()
        self.settings["withny_remux"] = self.withny_remux_var.get()
        self.settings["withny_remux_format"] = self.withny_remux_format_menu.get()
        self.settings["withny_concat"] = self.withny_concat_var.get()
        self.settings["withny_keep_intermediates"] = self.withny_keep_var.get()
        self.settings["withny_wait_poll_interval"] = self.withny_wait_entry.get().strip()
        self.settings["fc2_quality"] = self.fc2_quality_menu.get()
        self.settings["fc2_format"] = self.fc2_format_menu.get()
        self.settings["user_agent"] = self.ua_entry.get().strip()
        
        if self.save_settings():
            self.settings_dirty = False
            self.update_sleep_prevention_state()
            
            # Rplay Token inspection & diagnostic hint
            rplay_tok = self.settings.get("rplay_token", "")
            if rplay_tok:
                try:
                    import base64
                    import json
                    import time
                    parts = rplay_tok.split('.')
                    payload = None
                    if len(parts) == 3:
                        p_b64 = parts[1] + '=' * (4 - len(parts[1]) % 4)
                        payload = json.loads(base64.b64decode(p_b64).decode('utf-8'))
                    elif len(parts) == 1:
                        p_b64 = rplay_tok + '=' * (4 - len(rplay_tok) % 4)
                        payload = json.loads(base64.b64decode(p_b64).decode('utf-8'))
                    if payload and "exp" in payload:
                        exp_val = payload["exp"]
                        if time.time() > exp_val:
                            exp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp_val))
                            self.add_log(f"⚠️ 警告: 填入的 Rplay Token 已於 {exp_str} 過期，請至瀏覽器重新複製！", "WARNING")
                        else:
                            exp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp_val))
                            self.add_log(f"✅ Rplay Token 格式有效，到期時間: {exp_str}", "INFO")
                except:
                    pass
                    
            if getattr(self, 'paused_rplay_task', None) and rplay_tok:
                from utils import verify_rplay_token
                is_valid, user_or_err = verify_rplay_token(rplay_tok, self.settings.get("rplay_username"))
                if is_valid:
                    task_to_resume = self.paused_rplay_task
                    self.paused_rplay_task = None
                    self.resume_paused_rplay_task(task_to_resume)
                    
            messagebox.showinfo("💾 儲存成功", "設定已成功儲存並同步！")

    def on_settings_modified(self, *args):
        self.settings_dirty = True

    def reset_settings_fields_from_state(self):
        self.rplay_token_entry.delete(0, "end")
        self.rplay_token_entry.insert(0, self.settings.get("rplay_token", ""))
        
        self.rplay_username_entry.delete(0, "end")
        self.rplay_username_entry.insert(0, self.settings.get("rplay_username", ""))
        
        self.withny_token_entry.delete(0, "end")
        self.withny_token_entry.insert(0, self.settings.get("withny_token", ""))
        
        self.download_dir_entry.delete(0, "end")
        self.download_dir_entry.insert(0, self.settings.get("download_dir", ""))
        
        self.cookies_file_entry.delete(0, "end")
        self.cookies_file_entry.insert(0, self.settings.get("cookies_file", ""))
        
        self.fc2_cookies_file_entry.delete(0, "end")
        self.fc2_cookies_file_entry.insert(0, self.settings.get("fc2_cookies_file", ""))
        
        self.discord_bot_token_entry.delete(0, "end")
        self.discord_bot_token_entry.insert(0, self.settings.get("discord_bot_token", ""))
        
        self.discord_channel_id_entry.delete(0, "end")
        self.discord_channel_id_entry.insert(0, self.settings.get("discord_channel_id", ""))
        
        self.discord_completed_channel_id_entry.delete(0, "end")
        self.discord_completed_channel_id_entry.insert(0, self.settings.get("discord_completed_channel_id", ""))
        
        self.max_dl_spinner.delete(0, "end")
        self.max_dl_spinner.insert(0, str(self.settings.get("max_concurrent_downloads", 2)))
        
        cf = self.settings.get("concurrent_fragments", 8)
        found_cf = False
        for opt in ["8 通路 (推薦高速)", "16 通路 (極速)", "32 通路 (超速)", "4 通路 (標準)", "1 通路 (單線程)"]:
            if opt.startswith(f"{cf} "):
                self.concurrent_frags_menu.set(opt)
                found_cf = True
                break
        if not found_cf:
            self.concurrent_frags_menu.set(f"{cf} 通路")
        
        self.yt_quality_menu.set(self.settings.get("yt_quality", "best"))
        self.yt_format_menu.set(self.settings.get("yt_format", "mp4"))
        
        self.yt_keywords_entry.delete(0, "end")
        self.yt_keywords_entry.insert(0, ",".join(self.settings.get("yt_keywords", [])))
        
        self.rplay_quality_menu.set(self.settings.get("rplay_quality", "best"))
        self.rplay_format_menu.set(self.settings.get("rplay_format", "mp4"))
        self.withny_remux_var.set(self.settings.get("withny_remux", True))
        self.withny_remux_format_menu.set(self.settings.get("withny_remux_format", "mp4"))
        self.withny_concat_var.set(self.settings.get("withny_concat", False))
        self.withny_keep_var.set(self.settings.get("withny_keep_intermediates", False))
        
        self.withny_wait_entry.delete(0, "end")
        self.withny_wait_entry.insert(0, self.settings.get("withny_wait_poll_interval", "20s"))
        
        self.fc2_quality_menu.set(self.settings.get("fc2_quality", "3Mbps"))
        self.fc2_format_menu.set(self.settings.get("fc2_format", "mp4"))
        
        self.ua_entry.delete(0, "end")
        self.ua_entry.insert(0, self.settings.get("user_agent", ""))
        
        old_val = self.settings.get("prevent_sleep_mode", "下載/監控時阻擋睡眠")
        if old_val in ["下載時不休眠", "下載/監控時阻擋休眠"]:
            old_val = "下載/監控時阻擋睡眠"
        elif old_val in ["不休眠", "完全阻擋休眠"]:
            old_val = "完全阻擋睡眠"
        elif old_val in ["休眠", "恢復系統預設"]:
            old_val = "恢復系統預設"
        self.prevent_sleep_menu.set(old_val)

    # ================= Logs Tab =================
    def build_logs_tab(self):
        logs_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["logs"] = logs_frame
        logs_frame.grid_columnconfigure(0, weight=1)
        logs_frame.grid_rowconfigure(1, weight=1)
        
        # Header
        header_frame = ctk.CTkFrame(logs_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=12, pady=(10, 15), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(header_frame, text="📝 系統日誌與連線狀態", font=ctk.CTkFont(family="Segoe UI Variable Text", size=16, weight="bold"), text_color=self.c_text_primary).grid(row=0, column=0, sticky="w")
        
        clear_btn = ctk.CTkButton(header_frame, text="🧹 清空日誌", fg_color="#1e293b", hover_color="#334155", text_color=self.c_text_primary, height=30, corner_radius=6, font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"), command=self.clear_logs)
        clear_btn.grid(row=0, column=1, sticky="e")
        
        # Consolas console terminal log window
        self.log_textbox = ctk.CTkTextbox(
            logs_frame, 
            font=ctk.CTkFont(family="Consolas", size=13),
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            border_width=1,
            corner_radius=8,
            text_color=self.c_text_primary
        )
        self.log_textbox.grid(row=1, column=0, padx=12, pady=5, sticky="nsew")
        self.log_textbox.configure(state="disabled")
        
        # Setup specific console colors matching log levels
        self.log_textbox.tag_config("SUCCESS", foreground=self.c_green_text)
        self.log_textbox.tag_config("WARNING", foreground=self.c_yellow_text)
        self.log_textbox.tag_config("ERROR", foreground=self.c_red_text)
        self.log_textbox.tag_config("INFO", foreground=self.c_text_primary)

    def clear_logs(self):
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    # ================= Updates Tab =================
    def build_updates_tab(self):
        updates_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["updates"] = updates_frame
        updates_frame.grid_columnconfigure(0, weight=1)
        updates_frame.grid_rowconfigure(2, weight=1)
        
        # Header Area
        header_frame = ctk.CTkFrame(updates_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=12, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="🔄 系統元件與依賴更新", font=ctk.CTkFont(family="Segoe UI Variable Text", size=16, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        self.btn_check_updates = ctk.CTkButton(
            header_frame, 
            text="🔍 一鍵檢查所有更新", 
            fg_color=self.c_accent, 
            hover_color=self.c_accent_hover, 
            text_color=self.c_text_primary,
            height=32,
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI Variable Text", size=12, weight="bold"),
            command=self.check_all_updates_async
        )
        self.btn_check_updates.pack(side="right")
        
        # Component Grid / Scroll Frame
        scroll_updates = ctk.CTkScrollableFrame(updates_frame, fg_color=self.c_frame, border_color=self.c_card_border, border_width=1, corner_radius=12, height=310)
        scroll_updates.grid(row=1, column=0, padx=12, pady=5, sticky="ew")
        scroll_updates.grid_columnconfigure((0, 1), weight=1)
        
        # Components Data List
        self.comp_ui = {}
        
        components = [
            ("ytdlp", "🔴 yt-dlp 下載核心 (含原生擴展)", lambda: updater.get_local_version_ytdlp(self), self.update_ytdlp_async),
            ("ffmpeg", "🎞️ FFmpeg 解碼器", lambda: updater.get_local_version_ffmpeg(self), self.update_ffmpeg_async),
            ("rplay", "🔗 Rplay 原生核心 (Native)", lambda: updater.get_local_version_rplay(self), self.update_rplay_async),
            ("withnydl", "🟣 Withny 原生核心 (Native)", lambda: updater.get_local_version_withnydl(self), self.update_withnydl_async)
        ]
        
        form_label_font = ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold")
        
        for idx, (cid, name, loc_func, update_func) in enumerate(components):
            card = ctk.CTkFrame(
                scroll_updates, 
                fg_color=self.c_card, 
                border_color=self.c_card_border, 
                border_width=1, 
                corner_radius=8
            )
            r = idx // 2
            c = idx % 2
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")
            card.grid_columnconfigure(1, weight=1)
            
            # Content layout
            ctk.CTkLabel(card, text=name, font=ctk.CTkFont(family="Segoe UI Variable Text", size=13, weight="bold"), text_color=self.c_text_primary).grid(row=0, column=0, columnspan=3, padx=12, pady=(10, 4), sticky="w")
            
            # Local Version Info
            ctk.CTkLabel(card, text="本機版本:", font=form_label_font, text_color=self.c_text_secondary).grid(row=1, column=0, padx=12, pady=2, sticky="w")
            lbl_loc = ctk.CTkLabel(card, text="檢測中...", font=ctk.CTkFont(family="Segoe UI Variable Text", size=11), text_color=self.c_text_primary)
            lbl_loc.grid(row=1, column=1, padx=4, pady=2, sticky="w")
            
            # Online Version Info
            ctk.CTkLabel(card, text="最新線上:", font=form_label_font, text_color=self.c_text_secondary).grid(row=2, column=0, padx=12, pady=2, sticky="w")
            
            lbl_online = ctk.CTkLabel(card, text="未檢查", font=ctk.CTkFont(family="Segoe UI Variable Text", size=11), text_color=self.c_text_secondary)
            lbl_online.grid(row=2, column=1, padx=4, pady=2, sticky="w")
            if cid == "ytdlp":
                self.lbl_online_ytdlp = lbl_online
            elif cid == "ffmpeg":
                self.lbl_online_ffmpeg = lbl_online
            elif cid == "rplay":
                self.lbl_online_rplay = lbl_online
            elif cid == "withnydl":
                self.lbl_online_withnydl = lbl_online
                
            # Status Badge indicator
            lbl_status = ctk.CTkLabel(card, text="未檢查 ⚪", text_color=self.c_text_muted, font=ctk.CTkFont(family="Segoe UI Variable Text", size=10, weight="bold"))
            lbl_status.grid(row=1, column=2, rowspan=2, padx=12, pady=2, sticky="e")
            if cid == "ytdlp":
                self.lbl_status_ytdlp = lbl_status
            elif cid == "ffmpeg":
                self.lbl_status_ffmpeg = lbl_status
            elif cid == "rplay":
                self.lbl_status_rplay = lbl_status
            elif cid == "withnydl":
                self.lbl_status_withnydl = lbl_status
                
            # Update execution button
            btn_update = ctk.CTkButton(
                card, 
                text="更新", 
                fg_color=self.c_accent, 
                hover_color=self.c_accent_hover,
                text_color=self.c_text_primary,
                font=ctk.CTkFont(family="Segoe UI Variable Text", size=11, weight="bold"),
                height=28,
                corner_radius=6,
                command=update_func
            )
            btn_update.grid(row=3, column=0, columnspan=3, padx=12, pady=(6, 12), sticky="ew")
            
            self.comp_ui[cid] = {
                "lbl_loc": lbl_loc,
                "lbl_online": lbl_online,
                "lbl_status": lbl_status,
                "btn_update": btn_update,
                "loc_func": loc_func
            }
            
        # Logging text terminal for Updates
        self.updates_textbox = ctk.CTkTextbox(
            updates_frame, 
            font=ctk.CTkFont(family="Consolas", size=13),
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            border_width=1,
            text_color=self.c_text_primary
        )
        self.updates_textbox.grid(row=2, column=0, padx=15, pady=(10, 15), sticky="nsew")
        self.updates_textbox.configure(state="disabled")

    def updates_log(self, msg):
        self.updates_textbox.configure(state="normal")
        self.updates_textbox.insert("end", msg)
        self.updates_textbox.see("end")
        self.updates_textbox.configure(state="disabled")

    def disable_update_buttons(self):
        self.btn_check_updates.configure(state="disabled")
        for comp in self.comp_ui.values():
            comp["btn_update"].configure(state="disabled")
            
    def enable_update_buttons(self):
        self.btn_check_updates.configure(state="normal")
        for comp in self.comp_ui.values():
            comp["btn_update"].configure(state="normal")

    def check_safe_to_update(self):
        if self.is_monitoring:
            messagebox.showwarning("警告", "系統監控執行中！請先停止監控再更新元件。")
            return False
        has_active_tasks = any(
            t.get("process") and t["process"].poll() is None 
            for t in self.active_tasks.values()
        )
        if has_active_tasks:
            messagebox.showwarning("警告", "目前有下載任務正在執行！請等下載完成或停止任務後再更新。")
            return False
        return True

    def refresh_local_versions_ui(self):
        for cid, comp in self.comp_ui.items():
            comp["lbl_loc"].configure(text=comp["loc_func"]())

    def detect_local_versions_async(self):
        loc_ytdlp = updater.get_local_version_ytdlp(self)
        loc_ffmpeg = updater.get_local_version_ffmpeg(self)
        loc_rplay = updater.get_local_version_rplay(self)
        loc_withnydl = updater.get_local_version_withnydl(self)
        self.gui_update_queue.put(("local_versions_detected", (loc_ytdlp, loc_ffmpeg, loc_rplay, loc_withnydl)))

    def check_all_updates_async(self):
        self.updates_log("🔍 開始檢查所有元件的最新版本...\n")
        self.btn_check_updates.configure(state="disabled")
        
        self.lbl_online_ytdlp.configure(text="檢查中...", text_color=self.c_yellow)
        self.lbl_online_ffmpeg.configure(text="檢查中...", text_color=self.c_yellow)
        self.lbl_online_rplay.configure(text="檢查中...", text_color=self.c_yellow)
        self.lbl_online_withnydl.configure(text="檢查中...", text_color=self.c_yellow)
        
        threading.Thread(target=updater.worker_check_updates, args=(self,), daemon=True).start()

    def update_ytdlp_async(self):
        if not self.check_safe_to_update():
            return
        self.updates_log("🔔 開始更新 yt-dlp...\n")
        self.disable_update_buttons()
        threading.Thread(target=updater.worker_update_ytdlp, args=(self,), daemon=True).start()

    def update_ffmpeg_async(self):
        if not self.check_safe_to_update():
            return
        self.updates_log("🔔 開始更新 FFmpeg...\n")
        self.disable_update_buttons()
        threading.Thread(target=updater.worker_update_ffmpeg, args=(self,), daemon=True).start()

    def update_rplay_async(self):
        if not self.check_safe_to_update():
            return
        self.updates_log("🔔 開始更新 Rplay 下載核心...\n")
        self.disable_update_buttons()
        threading.Thread(target=updater.worker_update_rplay, args=(self,), daemon=True).start()

    def update_withnydl_async(self):
        if not self.check_safe_to_update():
            return
        self.updates_log("🔔 開始更新 Withny-dl...\n")
        self.disable_update_buttons()
        threading.Thread(target=updater.worker_update_withnydl, args=(self,), daemon=True).start()

    # ================= Background Thread Event Poller =================
    def poll_gui_updates(self):
        # 1. Process standard log entries
        while not self.log_queue.empty():
            try:
                log_msg, level = self.log_queue.get_nowait()
                self.log_textbox.configure(state="normal")
                self.log_textbox.insert("end", log_msg + "\n", level)
                self.log_textbox.see("end")
                self.log_textbox.configure(state="disabled")
            except queue.Empty:
                break
                
        # 2. Process asynchronous GUI events
        while not self.gui_update_queue.empty():
            try:
                evt_type, data = self.gui_update_queue.get_nowait()
                
                if evt_type == "image_preview_success":
                    self.image_preview_label.configure(text="", image=data)
                elif evt_type == "image_preview_fail":
                    self.image_preview_label.configure(text=data, image=None)
                elif evt_type == "refresh_tasks":
                    self.refresh_tasks_ui()
                elif evt_type == "refresh_history":
                    self.refresh_history_ui()
                elif evt_type == "refresh_channels_list":
                    self.refresh_channel_list_ui()
                elif evt_type == "thumbnail_loaded":
                    img_url, photo = data
                    for chan in self.channels:
                        if chan.get("image", "").strip() == img_url:
                            c_url = chan["url"]
                            if c_url in self.channel_card_widgets:
                                self.channel_card_widgets[c_url]["thumbnail"].configure(image=photo, text="")
                elif evt_type == "local_versions_detected":
                    loc_ytdlp, loc_ffmpeg, loc_rplay, loc_withnydl = data
                    self.comp_ui["ytdlp"]["lbl_loc"].configure(text=loc_ytdlp)
                    self.comp_ui["ffmpeg"]["lbl_loc"].configure(text=loc_ffmpeg)
                    self.comp_ui["rplay"]["lbl_loc"].configure(text=loc_rplay)
                    self.comp_ui["withnydl"]["lbl_loc"].configure(text=loc_withnydl)
                elif evt_type == "check_updates_done":
                    latest_ytdlp, latest_ffmpeg, latest_rplay, latest_withnydl = data
                    
                    # yt-dlp
                    self.lbl_online_ytdlp.configure(text=latest_ytdlp, text_color=self.c_text_primary)
                    loc_ytdlp = updater.get_local_version_ytdlp(self)
                    if latest_ytdlp != "未知" and loc_ytdlp != "未偵測到":
                        if loc_ytdlp == latest_ytdlp or loc_ytdlp in latest_ytdlp or latest_ytdlp in loc_ytdlp:
                            self.lbl_status_ytdlp.configure(text="最新版", text_color=self.c_green)
                        else:
                            self.lbl_status_ytdlp.configure(text="可更新 🔔", text_color=self.c_red)
                    else:
                        self.lbl_status_ytdlp.configure(text="未偵測到", text_color=self.c_text_secondary)
                        
                    # FFmpeg
                    self.lbl_online_ffmpeg.configure(text=latest_ffmpeg, text_color=self.c_text_primary)
                    loc_ffmpeg = updater.get_local_version_ffmpeg(self)
                    if latest_ffmpeg != "未知" and loc_ffmpeg != "未偵測到":
                        if loc_ffmpeg == latest_ffmpeg or loc_ffmpeg in latest_ffmpeg or latest_ffmpeg in loc_ffmpeg:
                            self.lbl_status_ffmpeg.configure(text="最新版", text_color=self.c_green)
                        else:
                            self.lbl_status_ffmpeg.configure(text="可更新 🔔", text_color=self.c_red)
                    else:
                        self.lbl_status_ffmpeg.configure(text="未偵測到", text_color=self.c_text_secondary)
                        
                    # Rplay
                    self.lbl_online_rplay.configure(text=latest_rplay, text_color=self.c_text_primary)
                    loc_rplay = updater.get_local_version_rplay(self)
                    if latest_rplay != "未知" and loc_rplay != "未偵測到":
                        if loc_rplay == latest_rplay or loc_rplay in latest_rplay or latest_rplay in loc_rplay:
                            self.lbl_status_rplay.configure(text="最新版", text_color=self.c_green)
                        else:
                            self.lbl_status_rplay.configure(text="可更新 🔔", text_color=self.c_red)
                    else:
                        self.lbl_status_rplay.configure(text="未偵測到", text_color=self.c_text_secondary)
                        
                    # Withny-dl
                    self.lbl_online_withnydl.configure(text=latest_withnydl, text_color=self.c_text_primary)
                    loc_withnydl = updater.get_local_version_withnydl(self)
                    if latest_withnydl != "未知" and loc_withnydl != "未偵測到":
                        if loc_withnydl == latest_withnydl or loc_withnydl in latest_withnydl or latest_withnydl in loc_withnydl:
                            self.lbl_status_withnydl.configure(text="最新版", text_color=self.c_green)
                        else:
                            self.lbl_status_withnydl.configure(text="可更新 🔔", text_color=self.c_red)
                    else:
                        self.lbl_status_withnydl.configure(text="未偵測到", text_color=self.c_text_secondary)
                        
                    self.btn_check_updates.configure(state="normal")
                    self.add_log("線上版本檢查完成！")
                    
                elif evt_type == "update_done":
                    comp_name, version = data
                    self.add_log(f"更新成功: {comp_name} ({version})", "SUCCESS")
                    self.refresh_local_versions_ui()
                    self.enable_update_buttons()
                    # Trigger checking to refresh statuses
                    self.check_all_updates_async()
                elif evt_type == "update_failed":
                    comp_name = data
                    self.add_log(f"更新失敗: {comp_name}", "ERROR")
                    self.enable_update_buttons()
                elif evt_type == "refresh_settings_ui":
                    self.reset_settings_fields_from_state()
                elif evt_type == "start_monitoring":
                    self.start_monitoring()
                elif evt_type == "stop_monitoring":
                    self.stop_monitoring()
            except queue.Empty:
                break
                
        # Update sleep prevention state dynamically
        self.update_sleep_prevention_state()
                
        # Schedule next poll
        self.after(100, self.poll_gui_updates)

    def start_periodic_task_timer(self):
        current_time = time.time()
        any_active = False
        for uid, task in list(self.active_tasks.items()):
            status = task.get("status", "")
            if status not in ["佇列中", "已完成", "失敗"]:
                any_active = True
                start_time = task.get("start_time")
                if start_time:
                    elapsed_sec = int(current_time - start_time)
                    hours = elapsed_sec // 3600
                    mins = (elapsed_sec % 3600) // 60
                    secs = elapsed_sec % 60
                    if hours > 0:
                        task["elapsed"] = f"{hours:02d}:{mins:02d}:{secs:02d}"
                    else:
                        task["elapsed"] = f"{mins:02d}:{secs:02d}"
                        
                    # Discord status message updates (every 60 seconds)
                    if task.get("discord_message_id"):
                        last_update = task.get("last_discord_update_sec", 0)
                        if last_update == 0 or (elapsed_sec - last_update >= 60):
                            task["last_discord_update_sec"] = elapsed_sec
                            self.discord_notify_update(uid)
                            
        if any_active:
            self.refresh_tasks_ui()
            
        self.after(1000, self.start_periodic_task_timer)

    # ================= Sleep Prevention & Cleanups =================
    def update_sleep_prevention_state(self):
        mode = self.settings.get("prevent_sleep_mode", "下載/監控時阻擋睡眠")
        
        has_active_downloads = False
        if mode in ["下載/監控時阻擋睡眠", "下載/監控時阻擋休眠", "下載時不休眠"]:
            if self.is_monitoring:
                has_active_downloads = True
            else:
                for task in self.active_tasks.values():
                    if task.get("process") and task["process"].poll() is None:
                        has_active_downloads = True
                        break
        
        target_prevent = False
        if mode in ["完全阻擋睡眠", "完全阻擋休眠", "不休眠"]:
            target_prevent = True
        elif mode in ["下載/監控時阻擋睡眠", "下載/監控時阻擋休眠", "下載時不休眠"]:
            target_prevent = has_active_downloads
        else: # "恢復系統預設" / "休眠"
            target_prevent = False
            
        current_prevent = getattr(self, "sleep_prevented_active", None)
        if current_prevent != target_prevent:
            self.sleep_prevented_active = target_prevent
            if target_prevent:
                self.apply_prevent_sleep()
            else:
                self.apply_restore_sleep()

    def apply_prevent_sleep(self):
        if sys.platform == "win32":
            try:
                import ctypes
                # Prevent both System Sleep (0x00000001) and Display Sleep (0x00000002) in continuous mode (0x80000000)
                # This is critical on Windows 10/11 with Modern Standby (S0) to keep the downloader thread active.
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
                self.add_log("防睡眠機制已啟用 (阻斷電腦自動睡眠與螢幕關閉)", "SUCCESS")
            except Exception as e:
                self.add_log(f"防睡眠機制啟用失敗: {e}", "WARNING")

    def apply_restore_sleep(self):
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
                self.add_log("防睡眠機制已解除 (恢復系統休眠與螢幕設定)", "INFO")
            except:
                pass

    def prevent_sleep(self):
        # Kept for compatibility, redirects to dynamic state update
        self.update_sleep_prevention_state()

    def restore_sleep(self):
        # Redirects to clean restore state on shutdown
        self.apply_restore_sleep()

    def cleanup_temp_files(self):
        try:
            subprocess.Popen(["taskkill", "/F", "/IM", "withny-dl-windows-amd64.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except:
            pass
        for log_name in ["debug_rplay.txt", "debug_withny.txt"]:
            log_path = os.path.join(BASE_DIR, log_name)
            if os.path.exists(log_path):
                try:
                    os.remove(log_path)
                except:
                    pass
        temp_configs_path = os.path.join(BASE_DIR, "temp_configs")
        if os.path.exists(temp_configs_path):
            try:
                shutil.rmtree(temp_configs_path)
            except:
                pass

    # ================= Cookie Processing =================
    def prepare_clean_cookies(self):
        if not os.path.exists(self.settings["cookies_file"]):
            return None, 0
        try:
            with open(self.settings["cookies_file"], 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            valid_lines = ["# Netscape HTTP Cookie File\n"]
            target_domains = ['withny', 'youtube', 'google', 'rplay']
            count = 0
            for line in lines:
                if not line.strip() or line.strip().startswith('#'):
                    continue
                if any(d in line for d in target_domains):
                    valid_lines.append(line)
                    count += 1
            clean_path = os.path.join(tempfile.gettempdir(), "monitor_clean_cookies.txt")
            with open(clean_path, 'w', encoding='utf-8') as f:
                f.writelines(valid_lines)
            return clean_path, count
        except Exception as e:
            self.add_log(f"淨化 Cookie 發生錯誤: {e}", "WARNING")
            return None, 0

    def prepare_clean_fc2_cookies(self):
        fc2_path = self.settings.get("fc2_cookies_file", "")
        if not fc2_path or not os.path.exists(fc2_path):
            fc2_path = self.settings.get("cookies_file", "")
            
        if not fc2_path or not os.path.exists(fc2_path):
            return None, 0
            
        try:
            with open(fc2_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            valid_lines = ["# Netscape HTTP Cookie File\n"]
            target_domains = ['fc2']
            count = 0
            for line in lines:
                if not line.strip() or line.strip().startswith('#'):
                    continue
                if any(d in line for d in target_domains):
                    valid_lines.append(line)
                    count += 1
            clean_path = os.path.join(tempfile.gettempdir(), "monitor_clean_fc2_cookies.txt")
            with open(clean_path, 'w', encoding='utf-8') as f:
                f.writelines(valid_lines)
            return clean_path, count
        except Exception as e:
            self.add_log(f"淨化 FC2 Cookie 發生錯誤: {e}", "WARNING")
            return None, 0

    # ================= Discord Notification Helper =================
    def get_discord_credentials(self, use_completed=False):
        token = self.settings.get("discord_bot_token", "").strip().strip('"').strip("'")
        if token.lower().startswith("bot "):
            token = token[4:].strip().strip('"').strip("'")
        if use_completed:
            channel_id = self.settings.get("discord_completed_channel_id", "").strip().strip('"').strip("'")
            if not channel_id:
                channel_id = self.settings.get("discord_channel_id", "").strip().strip('"').strip("'")
        else:
            channel_id = self.settings.get("discord_channel_id", "").strip().strip('"').strip("'")
        return token, channel_id

    def discord_notify_start(self, uid, url, title, custom_name, platform, image_url=None, can_record=False):
        token, channel_id = self.get_discord_credentials(use_completed=False)
        if not token or not channel_id:
            return
            
        task = self.active_tasks.get(uid)
        if task:
            task["image_url"] = image_url
            task["channel_url"] = url
            if task.get("discord_message_id"):
                self.discord_notify_update(uid)
                return
            
        iso_timestamp = datetime.utcnow().isoformat() + "Z"
        embed = {
            "title": f"🔴 [直播中] {custom_name}",
            "url": url,
            "description": "⏳ 已下載時間: 0秒",
            "color": 16711680, # Red
            "footer": {"text": "每分鐘自動更新 • StreamBot"},
            "timestamp": iso_timestamp
        }
        if image_url:
            embed["image"] = {"url": image_url}
            
        payload = {"embeds": [embed]}
        if can_record:
            payload["components"] = [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 1,
                            "label": "🔴 開始錄製",
                            "custom_id": f"start_rec_{uid}"
                        }
                    ]
                }
            ]
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json"
        }
        
        def _send():
            try:
                import requests
                res = requests.post(f"https://discord.com/api/v10/channels/{channel_id}/messages", json=payload, headers=headers, timeout=10)
                if res.status_code in [200, 201]:
                    msg_id = res.json().get("id")
                    if uid in self.ended_discord_tasks:
                        self.ended_discord_tasks.discard(uid)
                        requests.delete(f"https://discord.com/api/v10/channels/{channel_id}/messages/{msg_id}", headers=headers, timeout=10)
                    elif uid in self.active_tasks:
                        self.active_tasks[uid]["discord_message_id"] = msg_id
                        self.active_tasks[uid]["last_discord_update_sec"] = 0
                    else:
                        requests.delete(f"https://discord.com/api/v10/channels/{channel_id}/messages/{msg_id}", headers=headers, timeout=10)
                else:
                    self.add_log(f"Discord Bot 傳送狀態訊息失敗 (HTTP {res.status_code}): {res.text}", "WARNING")
            except Exception as e:
                self.add_log(f"Discord Bot notify start failed: {e}", "WARNING")
                
        threading.Thread(target=_send, daemon=True).start()

    def discord_notify_update(self, uid):
        token, channel_id = self.get_discord_credentials(use_completed=False)
        if not token or not channel_id:
            return
            
        if uid in self.ended_discord_tasks:
            return
            
        task = self.active_tasks.get(uid)
        if not task:
            return
            
        msg_id = task.get("discord_message_id")
        if not msg_id:
            return
            
        status = task.get("status", "")
        is_notify_only = status == "直播中(僅通知)"
        
        elapsed_sec = int(time.time() - task.get("start_time", time.time()))
        duration_zh = format_duration_zh(elapsed_sec)
        url = task.get("channel_url") or task.get("url", "")
        image_url = task.get("image_url")
        
        iso_timestamp = datetime.utcnow().isoformat() + "Z"
        
        description = f"⏳ 已下載時間: {duration_zh}"
        if is_notify_only:
            description = f"⏳ 直播中 (僅通知) • 已進行: {duration_zh}"
            
        embed = {
            "title": f"🔴 [直播中] {task.get('channel_name')}",
            "url": url,
            "description": description,
            "color": 16711680,
            "footer": {"text": "每分鐘自動更新 • StreamBot"},
            "timestamp": iso_timestamp
        }
        if image_url:
            embed["image"] = {"url": image_url}
            
        payload = {"embeds": [embed]}
        if is_notify_only:
            payload["components"] = [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 1,
                            "label": "🔴 開始錄製",
                            "custom_id": f"start_rec_{uid}"
                        }
                    ]
                }
            ]
        elif task.get("was_notify_only"):
            payload["components"] = [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 2,
                            "label": "⏺️ 錄製中",
                            "custom_id": f"start_rec_{uid}",
                            "disabled": True
                        }
                    ]
                }
            ]
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json"
        }
        
        def _update():
            try:
                import requests
                res = requests.patch(f"https://discord.com/api/v10/channels/{channel_id}/messages/{msg_id}", json=payload, headers=headers, timeout=10)
                if res.status_code not in [200, 201]:
                    self.add_log(f"Discord Bot 更新狀態訊息失敗 (HTTP {res.status_code}): {res.text}", "WARNING")
            except Exception as e:
                print(f"Discord Bot notify update failed: {e}")
                
        threading.Thread(target=_update, daemon=True).start()

    def discord_notify_end(self, uid, msg_id=None, has_saved=True):
        token, status_channel_id = self.get_discord_credentials(use_completed=False)
        _, completed_channel_id = self.get_discord_credentials(use_completed=True)
        if not token:
            return
            
        self.ended_discord_tasks.add(uid)
        
        task = self.active_tasks.get(uid)
        if not msg_id and task:
            msg_id = task.get("discord_message_id")
            
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json"
        }
        
        payload_comp = None
        if task and has_saved:
            channel_name = task.get("channel_name", "未知頻道")
            platform = task.get("platform", "未知平台")
            url = task.get("channel_url") or task.get("url", "")
            
            elapsed_sec = int(time.time() - task.get("start_time", time.time()))
            duration_zh = format_duration_zh(elapsed_sec)
            
            iso_timestamp = datetime.utcnow().isoformat() + "Z"
            
            comp_embed = {
                "title": f"✅ [下載完成] {channel_name}",
                "description": f"**平台**: {platform}",
                "color": 3066993, # Green
                "fields": [
                    {"name": "錄製時間", "value": f"⏳ {duration_zh}", "inline": True}
                ],
                "footer": {"text": "下載完成 • StreamBot"},
                "timestamp": iso_timestamp
            }
            if url:
                comp_embed["fields"].append({"name": "直播網址", "value": f"[點此前往]({url})", "inline": False})
                
            payload_comp = {"embeds": [comp_embed]}
            
        def _delete_and_notify():
            try:
                import requests
                if payload_comp and completed_channel_id:
                    res_comp = requests.post(f"https://discord.com/api/v10/channels/{completed_channel_id}/messages", json=payload_comp, headers=headers, timeout=10)
                    if res_comp.status_code not in [200, 201]:
                        self.add_log(f"Discord Bot 傳送完成訊息失敗 (HTTP {res_comp.status_code}): {res_comp.text}", "WARNING")
                
                if msg_id and status_channel_id:
                    res = requests.delete(f"https://discord.com/api/v10/channels/{status_channel_id}/messages/{msg_id}", headers={"Authorization": f"Bot {token}"}, timeout=10)
                    if res.status_code not in [200, 204]:
                        is_unknown_msg = False
                        try:
                            res_json = res.json()
                            if res_json.get("code") == 10008 or "Unknown Message" in res.text:
                                is_unknown_msg = True
                        except:
                            pass
                        if not is_unknown_msg:
                            self.add_log(f"Discord Bot 刪除狀態訊息失敗 (HTTP {res.status_code}): {res.text}", "WARNING")
            except Exception as e:
                print(f"Discord Bot notify end failed: {e}")
                
        threading.Thread(target=_delete_and_notify, daemon=True).start()

    def discord_notify_simple(self, url, title, custom_name, platform="直播", image_url=None):
        return

    def start_discord_gateway(self):
        token, _ = self.get_discord_credentials()
        if token and (self.discord_gw_thread is None or not self.discord_gw_thread.is_alive()):
            self.discord_gw_thread = threading.Thread(target=self.run_discord_gateway, daemon=True)
            self.discord_gw_thread.start()

    def run_discord_gateway(self):
        token, _ = self.get_discord_credentials()
        if not token:
            return
            
        import websocket
        import json
        
        self.add_log("Discord Gateway: 正在連線以接收遠端指令並上線機器人...", "INFO")
        heartbeat_thread = None
        stop_heartbeat = threading.Event()
        current_intents = [33281] # Guilds (1) | Guild Messages (512) | Message Content (32768)
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                op = data.get("op")
                if op == 10: # Hello
                    interval = data["d"]["heartbeat_interval"] / 1000.0
                    stop_heartbeat.clear()
                    
                    def heartbeat():
                        while not stop_heartbeat.is_set():
                            time.sleep(interval)
                            if stop_heartbeat.is_set():
                                break
                            try:
                                ws.send(json.dumps({"op": 1, "d": None}))
                            except:
                                break
                                
                    nonlocal heartbeat_thread
                    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
                    heartbeat_thread.start()
                    
                    # Send Identify
                    identify_payload = {
                        "op": 2,
                        "d": {
                            "token": token,
                            "intents": current_intents[0],
                            "properties": {
                                "os": "windows",
                                "browser": "StreamBot",
                                "device": "StreamBot"
                            },
                            "presence": {
                                "status": "online",
                                "activities": [{
                                    "name": "!help | StreamBot 遙控",
                                    "type": 0
                                }],
                                "afk": False
                            }
                        }
                    }
                    ws.send(json.dumps(identify_payload))
                elif op == 9: # Invalid Session
                    self.add_log("Discord Gateway: 連線工作階段無效 (Invalid Session)！", "WARNING")
                elif op == 0: # Dispatch
                    t = data.get("t")
                    d = data.get("d")
                    if t == "MESSAGE_CREATE":
                        self.handle_discord_message(d)
                    elif t == "INTERACTION_CREATE":
                        self.handle_discord_interaction(d)
            except Exception as e:
                print(f"Discord gateway on_message error: {e}")
                
        def on_error(ws, error):
            self.add_log(f"Discord Gateway 連線出錯: {error}", "WARNING")
            
        should_reconnect = [True]
        
        def on_close(ws, close_status_code, close_msg):
            stop_heartbeat.set()
            if close_status_code:
                if close_status_code == 4004:
                    self.add_log("Discord Gateway 連線關閉 (4004): 驗證失敗！請檢查 Bot Token 是否正確。", "ERROR")
                    should_reconnect[0] = False
                elif close_status_code == 4014:
                    if current_intents[0] == 33281:
                        self.add_log("Discord Gateway: 缺少 Message Content 特權 Intent，切換至基本權限重試...", "WARNING")
                        current_intents[0] = 513 # Fallback without Message Content Intent
                    else:
                        self.add_log("Discord Gateway 連線關閉 (4014): 缺少特權 Intent！", "ERROR")
                        should_reconnect[0] = False
                else:
                    self.add_log(f"Discord Gateway 連線關閉 ({close_status_code}): {close_msg}", "WARNING")
            else:
                self.add_log("Discord Gateway 連線已關閉。", "INFO")
            
        def on_open(ws):
            self.add_log("Discord Gateway: 已建立連線，機器人在線並已啟用遠端指令監聽！", "SUCCESS")
            
        # Keep Gateway active 24/7 in background to process remote commands anytime
        while should_reconnect[0]:
            token, _ = self.get_discord_credentials()
            if not token:
                break
                
            self.discord_ws = websocket.WebSocketApp(
                "wss://gateway.discord.gg/?v=10&encoding=json",
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            
            self.discord_ws.run_forever()
            stop_heartbeat.set()
            if not should_reconnect[0]:
                break
            time.sleep(5) # Reconnect delay

    def handle_discord_message(self, d):
        import requests
        
        author = d.get("author", {})
        if author.get("bot"):
            return # Ignore bot messages
            
        content = d.get("content", "").strip()
        if not content:
            return
            
        # Check command prefixes (! or / or ！)
        if not (content.startswith("!") or content.startswith("/") or content.startswith("！")):
            return
            
        channel_id = d.get("channel_id")
        msg_id = d.get("id")
        token, _ = self.get_discord_credentials()
        if not token or not channel_id:
            return
            
        def reply(text="", embed=None):
            headers = {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json"
            }
            payload = {}
            if text:
                payload["content"] = text
            if embed:
                payload["embeds"] = [embed]
            payload["message_reference"] = {"message_id": msg_id}
            
            def _send():
                try:
                    requests.post(f"https://discord.com/api/v10/channels/{channel_id}/messages", json=payload, headers=headers, timeout=10)
                except Exception as e:
                    print(f"Discord command reply error: {e}")
            threading.Thread(target=_send, daemon=True).start()

        # Parse command & arguments
        parts = content.split()
        cmd_raw = parts[0]
        cmd_name = cmd_raw.lstrip("!/！").lower()
        args = parts[1:]

        # 1. 啟動監控
        if cmd_name in ("start", "啟動", "開啟", "監控", "啟動監控", "開啟監控"):
            if self.is_monitoring:
                reply("ℹ️ StreamBot 目前已經在監控中了！")
            else:
                self.gui_update_queue.put(("start_monitoring", None))
                self.add_log("🤖 [Discord 指令] 收到 !start 遠端指令，正在啟動全域監控...", "INFO")
                embed = {
                    "title": "🟢 全域監控已啟動",
                    "description": f"已成功啟動 StreamBot 全域監控！\n目前監控頻道數: **{len(self.channels)}** 個",
                    "color": 3066993, # Green
                    "footer": {"text": "StreamBot 遠端遙控"}
                }
                reply(embed=embed)

        # 2. 停止監控
        elif cmd_name in ("stop", "停止", "關閉", "停止監控", "關閉監控"):
            if not self.is_monitoring:
                reply("ℹ️ StreamBot 目前處於靜止待命狀態，未在監控中。")
            else:
                self.gui_update_queue.put(("stop_monitoring", None))
                self.add_log("🤖 [Discord 指令] 收到 !stop 遠端指令，正在停止全域監控...", "INFO")
                embed = {
                    "title": "🛑 全域監控已停止",
                    "description": "已成功停止 StreamBot 全域監控，系統進入待命狀態。",
                    "color": 15158332, # Red
                    "footer": {"text": "StreamBot 遠端遙控"}
                }
                reply(embed=embed)

        # 3. 手動下載
        elif cmd_name in ("dl", "download", "下載", "手動下載"):
            if len(args) < 1:
                reply("⚠️ 請提供要下載的直播/影片網址！\n**使用範例**: `!dl https://rplay.live/live/... [自訂名稱]`")
                return
            target_url = args[0].strip()
            custom_name = " ".join(args[1:]).strip() if len(args) > 1 else "Discord_Manual_Download"
            ok, msg = self.start_api_manual_download(target_url, custom_name)
            if ok:
                self.add_log(f"🤖 [Discord 指令] 收到 !dl 遠端手動下載指令: {target_url}", "INFO")
                plat = detect_platform(target_url)
                embed = {
                    "title": "🚀 手動下載任務已指派",
                    "description": f"**平台**: {plat}\n**名稱**: {custom_name}\n**網址**: [點此前往]({target_url})",
                    "color": 10181046, # Purple
                    "footer": {"text": "StreamBot 手動下載"}
                }
                reply(embed=embed)
            else:
                reply(f"❌ 手動下載指派失敗: {msg}")

        # 4. 查看狀態
        elif cmd_name in ("status", "狀態", "info"):
            active_count = len(self.active_tasks)
            status_text = "🟢 監控中 (即時輪詢各頻道)" if self.is_monitoring else "🔴 待命中 (全域監控已關閉)"
            tasks_desc = ""
            if active_count > 0:
                tasks_desc = "\n\n**⚡ 進行中任務**:\n"
                for uid, t in list(self.active_tasks.items()):
                    pct = int(t.get("progress", 0) * 100) if t.get("progress", 0) >= 0 else ""
                    pct_str = f" ({pct}%)" if pct != "" else ""
                    tasks_desc += f"• **{t.get('channel_name', '未知')}** [{t.get('platform', '')}]: `{t.get('status', '')}`{pct_str} {t.get('speed', '')}\n"
            else:
                tasks_desc = "\n\n目前無正在錄影或下載的任務。"

            embed = {
                "title": "📊 StreamBot 運行狀態",
                "description": f"**監控狀態**: {status_text}\n**監控頻道數**: {len(self.channels)} 個\n**執行中任務**: {active_count} 個{tasks_desc}",
                "color": 3447003 if self.is_monitoring else 15158332,
                "footer": {"text": "StreamBot Status"}
            }
            reply(embed=embed)

        # 5. 查詢電腦 IP 與遠端遙控/同步位址
        elif cmd_name in ("ip", "位址", "網址", "lan"):
            lan_ip = self.get_current_lan_ip()
            embed = {
                "title": "🌐 StreamBot 區域網路連線與遠端位址",
                "description": f"目前電腦區域網路 IP: **`{lan_ip}`**\n（同 Wi-Fi 下手機或設備可透過以下網址進行遙控與同步）",
                "color": 49151, # Cyan
                "fields": [
                    {
                        "name": "📱 Web 遠端遙控網頁",
                        "value": f"[http://{lan_ip}:18730/](http://{lan_ip}:18730/)\n*(本機: `http://127.0.0.1:18730/`)*",
                        "inline": False
                    },
                    {
                        "name": "🎵 Rplay 手機/跨裝置同步位址",
                        "value": f"`http://{lan_ip}:18730/update_token`",
                        "inline": False
                    },
                    {
                        "name": "🪐 Withny 手機/跨裝置同步位址",
                        "value": f"`http://{lan_ip}:18730/update_withny_token`",
                        "inline": False
                    }
                ],
                "footer": {"text": "StreamBot IP & Remote Links"}
            }
            reply(embed=embed)

        # 6. 指令說明
        elif cmd_name in ("help", "指令", "說明", "h"):
            embed = {
                "title": "🤖 StreamBot Discord 指令清單",
                "description": "您可以在 Discord 頻道輸入以下指令遠端控制 StreamBot：",
                "color": 3447003,
                "fields": [
                    {"name": "▶️ 啟動全域監控", "value": "`!start` 或 `!監控`", "inline": False},
                    {"name": "🛑 停止全域監控", "value": "`!stop` 或 `!停止`", "inline": False},
                    {"name": "🚀 手動下載直播/影片", "value": "`!dl <網址> [名稱]` 或 `!download <網址>`", "inline": False},
                    {"name": "🌐 查詢電腦 IP 與遠端位址", "value": "`!ip` 或 `!位址`", "inline": False},
                    {"name": "📊 查看即時狀態與任務", "value": "`!status` 或 `!狀態`", "inline": False},
                    {"name": "❓ 查看指令說明", "value": "`!help` 或 `!指令`", "inline": False}
                ],
                "footer": {"text": "StreamBot Discord 遠端控制器"}
            }
            reply(embed=embed)

    def handle_discord_interaction(self, d):
        import requests
        
        interaction_id = d.get("id")
        token = d.get("token")
        
        # Acknowledge the interaction immediately to avoid client timeout errors
        try:
            requests.post(
                f"https://discord.com/api/v10/interactions/{interaction_id}/{token}/callback",
                json={"type": 6},
                headers={"Content-Type": "application/json"},
                timeout=5
            )
        except Exception as e:
            self.add_log(f"回應 Discord 互動失敗: {e}", "WARNING")
            
        int_data = d.get("data", {})
        custom_id = int_data.get("custom_id", "")
        if custom_id.startswith("start_rec_"):
            uid = custom_id[len("start_rec_"):]
            
            task = self.active_tasks.get(uid)
            if task and task.get("status") == "直播中(僅通知)":
                self.add_log(f"收到 Discord 錄製指令：正在為 [{task.get('channel_name')}] 啟動錄影...", "SUCCESS")
                
                # Transition status and reference target to enable recording
                task["was_notify_only"] = True
                task["status"] = "準備錄影"
                
                target = task.get("target_ref")
                if target:
                    target["record"] = True
                                
                self.discord_notify_update(uid)
                self.gui_update_queue.put(("refresh_tasks", None))
                self.gui_update_queue.put(("refresh_channels_list", None))

    # ================= Monitoring Control =================
    def toggle_monitoring(self):
        if self.is_monitoring:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self):
        if not self.channels:
            self.add_log("啟動監控失敗：沒有設定監控的頻道！", "WARNING")
            return
            
        self.monitor_session_id += 1
        self.is_monitoring = True
        self.monitor_btn.configure(text="🛑 停止全域監控", fg_color=self.c_red, hover_color="#e11d48")
        if hasattr(self, "sidebar_status_badge"):
            self.sidebar_status_badge.configure(text="● 監控運行中", text_color=self.c_blue_text)
        self.add_log("系統開始全域頻道監控...", "INFO")
        
        # Check Rplay credentials on startup / monitoring launch asynchronously
        threading.Thread(target=self.check_rplay_credentials_on_startup, daemon=True).start()
        
        # Ensure Discord Gateway is running for status and remote command control
        self.start_discord_gateway()
        
        clean_path, count = self.prepare_clean_cookies()
        if clean_path:
            self.add_log(f"已淨化並載入 Cookie ({count} 筆)", "SUCCESS")
            
        self.monitor_threads = []
        
        for idx, channel in enumerate(self.channels):
            uid = f"mon_{channel['name']}_{idx}"
            
            target = {
                "uid": uid,
                "name": channel["name"],
                "url": channel["url"],
                "record": channel["record"],
                "image": channel["image"]
            }
            
            url_lower = channel["url"].lower()
            th = None
            if "rplay.live" in url_lower:
                th = threading.Thread(target=workers.worker_rplay, args=(self, target), daemon=True)
            elif "youtube.com" in url_lower or "youtu.be" in url_lower:
                th = threading.Thread(target=workers.worker_youtube, args=(self, target), daemon=True)
            elif "withny.fun" in url_lower:
                th = threading.Thread(target=workers.worker_withny, args=(self, target), daemon=True)
            elif "live.fc2.com" in url_lower:
                th = threading.Thread(target=workers.worker_fc2, args=(self, target), daemon=True)
                
            if th:
                th.start()
                self.monitor_threads.append(th)
                
        self.gui_update_queue.put(("refresh_channels_list", None))

    def stop_monitoring(self):
        self.is_monitoring = False
        
        # Close Discord Gateway connection to log bot offline
        if hasattr(self, "discord_ws") and self.discord_ws:
            try:
                self.discord_ws.close()
            except:
                pass
            self.discord_ws = None
            
        self.monitor_btn.configure(text="▶️ 啟動全域監控", fg_color=self.c_accent, hover_color=self.c_accent_hover)
        if hasattr(self, "sidebar_status_badge"):
            self.sidebar_status_badge.configure(text="● 系統就緒 (待機中)", text_color=self.c_green_text)
        self.add_log("停止監控中，正在重置連線與子程序...", "WARNING")
        
        # Kill all active task processes
        from utils import kill_process_tree, clean_dir_leftovers
        for uid, task in list(self.active_tasks.items()):
            proc = task.get("process")
            if proc:
                try:
                    kill_process_tree(proc)
                except Exception as e:
                    self.add_log(f"終止監控任務 [{task.get('channel_name')}] 子程序出錯: {e}", "ERROR")
                    
        self.cleanup_temp_files()
        
        # Clean up leftover temp files in the download directory
        try:
            if self.settings.get("download_dir"):
                clean_dir_leftovers(self.settings["download_dir"])
        except Exception as e:
            self.add_log(f"停止監控時清理暫存檔失敗: {e}", "WARNING")
            
        self.active_tasks.clear()
        self.gui_update_queue.put(("refresh_tasks", None))
        self.gui_update_queue.put(("refresh_channels_list", None))

    def check_rplay_credentials_on_startup(self):
        has_rplay = any("rplay.live" in c.get("url", "").lower() for c in self.channels)
        if not has_rplay:
            return
            
        rplay_token = self.settings.get("rplay_token", "").strip()
        rplay_user_oid = self.settings.get("rplay_username", "").strip()
        
        if not rplay_token:
            self.add_log("ℹ️ [啟動檢查] 未設定 Rplay Token，針對限定直播頻道可能無法錄製", "INFO")
            return
            
        import base64
        import json
        import time
        import requests
        
        try:
            parts = rplay_token.split('.')
            payload = None
            if len(parts) == 3:
                p_b64 = parts[1] + '=' * (4 - len(parts[1]) % 4)
                payload = json.loads(base64.b64decode(p_b64).decode('utf-8'))
            elif len(parts) == 1:
                p_b64 = rplay_token + '=' * (4 - len(rplay_token) % 4)
                payload = json.loads(base64.b64decode(p_b64).decode('utf-8'))
                
            if payload and "exp" in payload:
                exp_val = payload["exp"]
                if time.time() > exp_val:
                    exp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp_val))
                    self.add_log(f"🚨 [啟動檢查] Rplay Token 已於 {exp_str} 過期，請至設定更新！", "ERROR")
                    return
        except Exception:
            pass

        if not rplay_user_oid:
            try:
                headers = {
                    'User-Agent': self.settings.get("user_agent", "Mozilla/5.0"),
                    'Referer': 'https://rplay.live/',
                    'Origin': 'https://rplay.live',
                    'Authorization': rplay_token
                }
                res = requests.get('https://api.rplay.live/account/getuser', headers=headers, timeout=5)
                if res.status_code == 200:
                    fetched_oid = res.json().get('_id') or res.json().get('id')
                    if fetched_oid:
                        rplay_user_oid = fetched_oid
                        self.settings["rplay_username"] = fetched_oid
                        self.save_settings()
                        self.add_log(f"✅ [啟動檢查] 自動補全 Rplay User OID: {fetched_oid}", "SUCCESS")
            except Exception:
                pass
                
        if not rplay_user_oid:
            self.add_log("⚠️ [啟動檢查] Rplay 已設定 Token 但缺少 User OID！", "WARNING")
            return
            
        try:
            headers = {
                'User-Agent': self.settings.get("user_agent", "Mozilla/5.0"),
                'Referer': 'https://rplay.live/',
                'Origin': 'https://rplay.live',
                'Authorization': rplay_token
            }
            key_url = f"https://api.rplay.live/live/key2?lang=en&requestorOid={rplay_user_oid}"
            res = requests.get(key_url, headers=headers, timeout=5)
            if res.status_code == 200:
                res_json = res.json()
                if res_json.get("region") == "japan" and not res_json.get("authKey"):
                    self.add_log(f"⚠️ [啟動檢查] Rplay 授權異常: User OID ({rplay_user_oid}) 與 Token 不匹配或頻道被限制！", "WARNING")
                else:
                    self.add_log(f"✅ [啟動檢查] Rplay 登入授權狀態正常 (OID: {rplay_user_oid})", "SUCCESS")
            elif res.status_code in (401, 403):
                self.add_log(f"🚨 [啟動檢查] Rplay 登入授權失敗 (HTTP {res.status_code})，請更新 Rplay Token！", "ERROR")
        except Exception as e:
            self.add_log(f"⚠️ [啟動檢查] Rplay 驗證連線失敗: {e}", "WARNING")

    def check_rplay_status(self, url):
        import re
        import requests
        
        headers = {
            'User-Agent': self.settings.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
            'Referer': 'https://rplay.live/',
            'Origin': 'https://rplay.live'
        }
        rplay_token = self.settings.get("rplay_token", "").strip()
        if rplay_token:
            headers['Authorization'] = rplay_token

        # 1. Rplay 影片/錄播存檔 (VOD / Play Content)
        m_play = re.search(r'https?://rplay\.live/(?:play|content|video)/(?P<id>[\d\w]+)', url)
        if m_play:
            content_id = m_play.group('id')
            try:
                from workers import get_rplay_butter_token
                butter = get_rplay_butter_token()
                c_headers = headers.copy()
                if butter:
                    c_headers['Butter'] = butter
                params = {
                    'contentOid': content_id,
                    'status': 'published',
                    'requestCanView': 'true'
                }
                rplay_user_oid = self.settings.get("rplay_username", "").strip()
                if rplay_user_oid:
                    params['requestorOid'] = rplay_user_oid
                res = requests.get('https://api.rplay.live/content', params=params, headers=c_headers, timeout=10)
                if res.status_code == 200:
                    cdata = res.json()
                    title = cdata.get('title', 'Rplay影片檔')
                    return {
                        'title': title,
                        'live_status': 'vod',
                        'is_live': False,
                        'content_data': cdata
                    }
            except Exception as e:
                self.add_log(f"Rplay 影片存檔查詢失敗 ({content_id}): {e}", "WARNING")
            return None

        # 2. Rplay 即時直播頻道 (Live Channel)
        m = re.match(r'https?://rplay\.live/(?P<short>c|live)/(?P<id>[\d\w]+)', url)
        if not m:
            return None
        short = m.group('short')
        url_id = m.group('id')
        creator_oid = url_id
        
        if short == 'c':
            try:
                res = requests.get(f'https://api.rplay.live/account/getuser?customUrl={url_id}', headers=headers, timeout=10)
                if res.status_code == 200:
                    creator_oid = res.json().get('_id')
                else:
                    return None
            except Exception as e:
                self.add_log(f"Rplay 解析帳號失敗 ({url_id}): {e}", "WARNING")
                return None
                
        try:
            res = requests.get(f'https://api.rplay.live/live/play?creatorOid={creator_oid}', headers=headers, timeout=10)
            if res.status_code == 200:
                live_info = res.json()
                state = live_info.get('streamState')
                if state == 'live':
                    return {
                        'title': live_info.get('title', '無標題'),
                        'live_status': 'is_live',
                        'is_live': True
                    }
                elif state in ('twitch', 'youtube'):
                    # Ignore Twitch/YouTube redirects
                    return None
            elif res.status_code in (401, 403):
                self.add_log(f"⚠️ Rplay 頻道查詢權限失敗 (HTTP {res.status_code})，請確認 Token 與 User OID 是否有效或已過期！", "WARNING")
        except Exception as e:
            self.add_log(f"Rplay 監控異常 ({url_id}): {e}", "WARNING")
            
        return None

    def find_newest_downloaded_file_in_dir(self, output_dir, start_time):
        if not os.path.exists(output_dir):
            return None
            
        newest_file = None
        newest_mtime = 0
        
        # Subtract a 120-second buffer to handle timing drift/file creation delays
        threshold_time = start_time - 120
        
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(file_path)
                    if mtime > threshold_time and mtime > newest_mtime:
                        # Exclude temporary intermediate TS files and temp mp4/mkv merge files
                        if not file.endswith(".part") and not file.endswith(".ytdl") and not file.endswith(".ts") and not file.endswith(".temp.mp4") and not file.endswith(".temp.mkv"):
                            newest_file = file_path
                            newest_mtime = mtime
                except:
                    pass
        return newest_file

    def find_newest_downloaded_file(self, channel_name, start_time):
        safe_name = sanitize_filename(channel_name)
        output_dir = os.path.join(self.settings["download_dir"], safe_name)
        return self.find_newest_downloaded_file_in_dir(output_dir, start_time)

# ================= System execution entrypoint =================
if __name__ == "__main__":
    app = App()
    
    def on_exit():
        if getattr(app, "settings_dirty", False):
            ans = messagebox.askyesnocancel("未儲存的變更", "您的設定已變更，但尚未儲存！\n是否要在結束前儲存設定變更？")
            if ans is True: # Yes
                app.apply_and_save_settings_gui()
            elif ans is None: # Cancel
                return
                
        if getattr(app, "channels_dirty", False):
            ans = messagebox.askyesnocancel("未儲存的變更", "您的頻道清單已變更，但尚未儲存！\n是否要在結束前儲存並寫入 channels.json？")
            if ans is True: # Yes
                app.write_channels_file()
            elif ans is None: # Cancel
                return

        app.stop_monitoring()
        app.restore_sleep()
        app.destroy()
        sys.exit(0)
        
    app.protocol("WM_DELETE_WINDOW", on_exit)
    app.mainloop()
