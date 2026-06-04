import sys
import os
import queue
import time
import tempfile
import threading
from datetime import datetime

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
        
        # Bind MouseWheel globally
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        
    def _on_content_configure(self, event):
        self.configure(scrollregion=self.bbox("all"))
        
    def _on_canvas_configure(self, event):
        self.itemconfig(self.window_id, width=event.width)
        
    def _on_mousewheel(self, event):
        is_descendant = False
        widget = event.widget
        try:
            while widget:
                if widget == self.container:
                    is_descendant = True
                    break
                widget = getattr(widget, "master", None)
        except Exception:
            pass
            
        if is_descendant and event.delta:
            self.yview_scroll(int(-1 * (event.delta / 120)), "units")
            
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
        
        # Color Palette Settings
        self.c_bg = "#0c1315"
        self.c_sidebar = "#080e10"
        self.c_frame = "#0d1719"
        self.c_card = "#111d20"
        self.c_card_border = "#1c3135"
        self.c_card_selected = "#142629"
        
        self.c_text_primary = "#e2ecec"
        self.c_text_secondary = "#789092"
        
        self.c_accent = "#136c72"
        self.c_accent_hover = "#18838a"
        
        self.c_green = "#10b981"
        self.c_green_bg = "#0d261e"
        self.c_red = "#ef4444"
        self.c_red_bg = "#251214"
        self.c_blue = "#3b82f6"
        self.c_blue_bg = "#0f1c2d"
        self.c_purple = "#8b5cf6"
        self.c_purple_bg = "#19152b"
        self.c_yellow = "#f59e0b"
        self.c_yellow_bg = "#24180d"
        
        # Configure root window background
        self.configure(fg_color=self.c_bg)
        
        # Window settings
        self.title("📡 全能直播監控下載器 (Stream Downloader & Channel Manager)")
        self.geometry("1300x820")
        self.minsize(1150, 720)
        
        # State variables
        self.settings = DEFAULT_SETTINGS.copy()
        self.channels = []
        self.history = []
        self.active_tasks = {} # uid -> task_info dict
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
        self.thumbnail_queue = queue.Queue()
        for _ in range(2):
            threading.Thread(target=self.worker_thumbnail_downloader, daemon=True).start()
        
        self.platform_colors = {
            "Rplay": (self.c_blue, self.c_blue_bg),
            "Withny": (self.c_purple, self.c_purple_bg),
            "YouTube": (self.c_red, self.c_red_bg),
            "FC2": (self.c_yellow, self.c_yellow_bg),
            "Unknown": (self.c_text_secondary, self.c_card)
        }
        
        # Load configs
        self.load_all_configs()
        
        # Build UI layout
        self.build_ui()
        
        # Start GUI polling
        self.poll_gui_updates()
        
        # Windows sleep prevention
        self.sleep_prevented_active = None
        self.update_sleep_prevention_state()
        
        # Auto start monitoring if channels loaded
        if self.channels:
            self.start_monitoring()

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
        print(log_msg)

    # ================= UI Build & Navigation =================
    def build_ui(self):
        # Grid layout config
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0) # Sidebar
        self.grid_columnconfigure(1, weight=1) # Main Viewport Container
        
        # 1. Left Sidebar Frame (styled matching reference image)
        sidebar = ctk.CTkFrame(self, width=220, fg_color=self.c_sidebar, corner_radius=0, border_color="#121b1d", border_width=1)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(6, weight=1)
        
        # Logo Label
        logo_lbl = ctk.CTkLabel(sidebar, text="📡 SDL MONITOR", font=ctk.CTkFont(size=20, weight="bold"), text_color=self.c_text_primary)
        logo_lbl.grid(row=0, column=0, padx=20, pady=25, sticky="w")
        
        # Nav buttons (Pill shape styled active status)
        tabs = [
            ("dashboard", "📊  監控列表"),
            ("tasks", "📥  下載任務"),
            ("history", "📜  下載紀錄"),
            ("manual", "🔗  手動下載"),
            ("settings", "⚙️  系統設定"),
            ("updates", "🔄  系統更新"),
            ("logs", "📝  系統日誌")
        ]
        
        self.nav_buttons = {}
        for idx, (tab_id, title) in enumerate(tabs):
            btn = ctk.CTkButton(
                sidebar,
                text=title,
                height=42,
                anchor="w",
                fg_color="transparent" if tab_id != "dashboard" else self.c_accent,
                text_color=self.c_text_secondary if tab_id != "dashboard" else self.c_text_primary,
                font=ctk.CTkFont(size=13, weight="bold"),
                hover_color=self.c_accent_hover,
                corner_radius=8,
                command=lambda tid=tab_id: self.select_tab(tid)
            )
            btn.grid(row=idx+1, column=0, padx=12, pady=5, sticky="ew")
            self.nav_buttons[tab_id] = btn
            
        # Left Bottom Monitoring Control Block
        self.monitor_btn = ctk.CTkButton(
            sidebar, 
            text="▶️ 啟動監控", 
            height=45, 
            fg_color=self.c_accent, 
            hover_color=self.c_accent_hover, 
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.toggle_monitoring
        )
        self.monitor_btn.grid(row=8, column=0, padx=12, pady=15, sticky="ew")
        
        # 2. Main Viewport Container
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
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
                self.nav_buttons["settings"].configure(fg_color=self.c_accent, text_color=self.c_text_primary)
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
                self.nav_buttons["dashboard"].configure(fg_color=self.c_accent, text_color=self.c_text_primary)
                if tab_id in self.nav_buttons:
                    self.nav_buttons[tab_id].configure(fg_color="transparent", text_color=self.c_text_secondary)
                return
                
        self.current_tab = tab_id

        # Visual feedback on sidebar buttons matching the active status
        for tid, btn in self.nav_buttons.items():
            if tid == tab_id:
                btn.configure(fg_color=self.c_accent, text_color=self.c_text_primary)
            else:
                btn.configure(fg_color="transparent", text_color=self.c_text_secondary)
                
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
        list_panel.grid_rowconfigure(1, weight=1)
        list_panel.grid_columnconfigure(0, weight=1)
        self.list_panel = list_panel
        
        # List Panel Search Bar
        search_frame = ctk.CTkFrame(list_panel, fg_color="transparent")
        search_frame.grid(row=0, column=0, padx=16, pady=16, sticky="ew")
        search_frame.grid_columnconfigure(1, weight=1)
        search_frame.grid_columnconfigure(2, weight=0)
        
        search_label = ctk.CTkLabel(search_frame, text="🔍 搜尋：", text_color=self.c_text_primary, font=ctk.CTkFont(weight="bold"))
        search_label.grid(row=0, column=0, padx=4, pady=0)
        self.search_entry = ctk.CTkEntry(
            search_frame, 
            placeholder_text="輸入名稱或網址以進行過濾...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            border_width=1,
            text_color=self.c_text_primary,
            placeholder_text_color="#4f6668"
        )
        self.search_entry.grid(row=0, column=1, padx=4, pady=0, sticky="ew")
        self.search_entry.bind("<KeyRelease>", self.filter_channels)

        self.toggle_edit_btn = ctk.CTkButton(
            search_frame,
            text="🛠 隱藏設定面板",
            fg_color="gray30",
            hover_color="gray40",
            text_color=self.c_text_primary,
            font=ctk.CTkFont(weight="bold"),
            width=100,
            command=self.toggle_edit_panel
        )
        self.toggle_edit_btn.grid(row=0, column=2, padx=(10, 4), pady=0)
        self.edit_panel_visible = True
        
        # Channels Scrollable Frame
        self.channels_scroll_frame = NativeScrollableFrame(list_panel, self.c_frame)
        self.channels_scroll_frame.grid(row=1, column=0, padx=12, pady=(0, 16), sticky="nsew")
        
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
        
        title_label = ctk.CTkLabel(edit_panel, text="🛠 頻道屬性設定", font=ctk.CTkFont(size=16, weight="bold"), text_color=self.c_text_primary)
        title_label.grid(row=0, column=0, padx=16, pady=16, sticky="w")
        
        # Form Fields
        fields_frame = ctk.CTkFrame(edit_panel, fg_color="transparent")
        fields_frame.grid(row=1, column=0, padx=16, pady=0, sticky="ew")
        fields_frame.grid_columnconfigure(1, weight=1)
        
        # Name
        lbl1 = ctk.CTkLabel(fields_frame, text="顯示名稱 (ID):", text_color=self.c_text_secondary, font=ctk.CTkFont(size=12))
        lbl1.grid(row=0, column=0, padx=4, pady=5, sticky="w")
        self.chan_name_entry = ctk.CTkEntry(
            fields_frame, 
            placeholder_text="例如: セラ",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            text_color=self.c_text_primary,
            placeholder_text_color="#4f6668"
        )
        self.chan_name_entry.grid(row=0, column=1, padx=4, pady=5, sticky="ew")
        
        # URL
        lbl2 = ctk.CTkLabel(fields_frame, text="頻道網址 (URL):", text_color=self.c_text_secondary, font=ctk.CTkFont(size=12))
        lbl2.grid(row=1, column=0, padx=4, pady=5, sticky="w")
        self.chan_url_entry = ctk.CTkEntry(
            fields_frame, 
            placeholder_text="輸入頻道直播或首頁網址...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            text_color=self.c_text_primary,
            placeholder_text_color="#4f6668"
        )
        self.chan_url_entry.grid(row=1, column=1, padx=4, pady=5, sticky="ew")
        self.chan_url_entry.bind("<KeyRelease>", self.on_chan_url_keyrelease)
        
        # Detected platform badge
        self.chan_platform_badge = ctk.CTkLabel(fields_frame, text="平台識別：未知 ⚪", font=ctk.CTkFont(weight="bold"), text_color=self.c_text_secondary)
        self.chan_platform_badge.grid(row=2, column=0, columnspan=2, padx=4, pady=8, sticky="w")
        
        # Archive Enable Toggle
        self.chan_record_var = ctk.BooleanVar(value=True)
        self.chan_record_switch = ctk.CTkSwitch(
            fields_frame, 
            text="啟用自動錄影功能", 
            variable=self.chan_record_var,
            text_color=self.c_text_primary,
            progress_color=self.c_accent,
            button_color=self.c_text_primary,
            button_hover_color=self.c_accent_hover
        )
        self.chan_record_switch.grid(row=3, column=0, columnspan=2, padx=4, pady=8, sticky="w")
        
        # Cover image URL
        lbl3 = ctk.CTkLabel(fields_frame, text="封面圖片網址:", text_color=self.c_text_secondary, font=ctk.CTkFont(size=12))
        lbl3.grid(row=4, column=0, padx=4, pady=5, sticky="w")
        self.chan_image_entry = ctk.CTkEntry(
            fields_frame, 
            placeholder_text="網址 (用於 Discord 通知)...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            text_color=self.c_text_primary,
            placeholder_text_color="#4f6668"
        )
        self.chan_image_entry.grid(row=4, column=1, padx=4, pady=5, sticky="ew")
        self.chan_image_entry.bind("<KeyRelease>", self.on_chan_image_keyrelease)
        
        # Image Preview Area
        preview_group = ctk.CTkFrame(edit_panel, fg_color=self.c_sidebar, border_color=self.c_card_border, border_width=1, corner_radius=8)
        preview_group.grid(row=2, column=0, padx=16, pady=16, sticky="nsew")
        preview_group.grid_rowconfigure(1, weight=1)
        preview_group.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(
            preview_group, 
            text="🖼 封面預覽", 
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.c_text_primary
        ).grid(row=0, column=0, padx=12, pady=6, sticky="w")
        
        self.image_preview_label = ctk.CTkLabel(preview_group, text="無圖片預覽", text_color=self.c_text_secondary)
        self.image_preview_label.grid(row=1, column=0, padx=12, pady=12, sticky="nsew")
        
        # Action Buttons for Editing Panel
        btn_frame = ctk.CTkFrame(edit_panel, fg_color="transparent")
        btn_frame.grid(row=3, column=0, padx=16, pady=4, sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        
        self.add_chan_btn = ctk.CTkButton(
            btn_frame, 
            text="✨ 新增頻道", 
            fg_color=self.c_green, 
            hover_color="#0fa472", 
            font=ctk.CTkFont(weight="bold"),
            command=self.add_channel
        )
        self.add_chan_btn.grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        
        self.update_chan_btn = ctk.CTkButton(
            btn_frame, 
            text="💾 更新選取", 
            state="disabled", 
            fg_color=self.c_accent,
            hover_color=self.c_accent_hover,
            font=ctk.CTkFont(weight="bold"),
            command=self.update_channel
        )
        self.update_chan_btn.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        
        btn_frame2 = ctk.CTkFrame(edit_panel, fg_color="transparent")
        btn_frame2.grid(row=4, column=0, padx=16, pady=4, sticky="ew")
        btn_frame2.grid_columnconfigure(0, weight=1)
        btn_frame2.grid_columnconfigure(1, weight=1)
        
        self.delete_chan_btn = ctk.CTkButton(
            btn_frame2, 
            text="🗑 刪除選取", 
            fg_color=self.c_red, 
            hover_color="#db3b3b", 
            font=ctk.CTkFont(weight="bold"),
            state="disabled", 
            command=self.delete_channel
        )
        self.delete_chan_btn.grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        
        self.clear_chan_btn = ctk.CTkButton(
            btn_frame2, 
            text="🧹 清空輸入", 
            fg_color="gray30", 
            hover_color="gray40", 
            text_color=self.c_text_primary,
            font=ctk.CTkFont(weight="bold"),
            command=self.clear_channel_form
        )
        self.clear_chan_btn.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        
        # Save Channels button at bottom
        self.save_chans_btn = ctk.CTkButton(
            edit_panel, 
            text="💾 儲存寫入 channels.json", 
            height=40, 
            font=ctk.CTkFont(size=13, weight="bold"), 
            fg_color="#009688", 
            hover_color="#00796b", 
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
                if utask.get("channel_name") == channel["name"]:
                    channel_state = utask.get("status", "Offline")
            
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
            if is_active_download:
                rec_text = f"🔴 {channel_state}"
                rec_color = self.c_red
                rec_bg = self.c_red_bg
            else:
                rec_text = "REC ON" if channel["record"] else "NOTIFY"
                rec_color = self.c_red if channel["record"] else self.c_blue
                rec_bg = self.c_red_bg if channel["record"] else self.c_blue_bg
                
            card_fg = self.c_card_selected if idx_in_channels == self.selected_channel_index else self.c_card
            card_border = self.c_accent if idx_in_channels == self.selected_channel_index else self.c_card_border
            
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
                card_info["rec_badge"].configure(text=rec_text, bg=rec_bg, fg=rec_color, width=12 if is_active_download else 9)
                
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
                thumb_container = tk.Frame(card, width=45, height=45, bg=self.c_sidebar)
                thumb_container.grid_propagate(False)
                thumb_container.grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=12)
                
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
                    font=("Microsoft JhengHei", 11, "bold"),
                    bg=card_fg,
                    fg=self.c_text_primary,
                    anchor="w"
                )
                name_label.grid(row=0, column=1, padx=(6, 12), pady=(12, 2), sticky="w")
                
                url_subtitle = channel["url"]
                if len(url_subtitle) > 42:
                    url_subtitle = url_subtitle[:39] + "..."
                url_label = tk.Label(
                    card, 
                    text=url_subtitle, 
                    font=("Microsoft JhengHei", 9),
                    bg=card_fg,
                    fg=self.c_text_secondary,
                    anchor="w"
                )
                url_label.grid(row=1, column=1, padx=(6, 12), pady=(2, 12), sticky="w")
                
                # 3. Platform Badge
                plat_badge = tk.Label(
                    card, 
                    text=platform.upper(), 
                    bg=plat_bg,
                    fg=plat_color, 
                    font=("Microsoft JhengHei", 8, "bold"),
                    width=10,
                    height=1,
                    relief="flat"
                )
                plat_badge.grid(row=0, column=2, rowspan=2, padx=10, pady=12)
                
                # 4. Record Badge
                rec_badge = tk.Label(
                    card, 
                    text=rec_text, 
                    bg=rec_bg,
                    fg=rec_color, 
                    font=("Microsoft JhengHei", 8, "bold"),
                    width=12 if is_active_download else 9,
                    height=1,
                    relief="flat"
                )
                rec_badge.grid(row=0, column=3, rowspan=2, padx=10, pady=12)
                
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
                
            card.pack(fill="x", padx=5, pady=5)
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
                border_col = self.c_accent if i == index else self.c_card_border
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
        header_frame.grid(row=0, column=0, padx=15, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="📥 正在下載的任務", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        # Tasks Scrollable Area
        self.tasks_scroll_frame = ctk.CTkScrollableFrame(
            tasks_frame, 
            fg_color=self.c_frame, 
            border_color=self.c_card_border, 
            border_width=1, 
            corner_radius=12
        )
        self.tasks_scroll_frame.grid(row=1, column=0, padx=15, pady=5, sticky="nsew")
        self.tasks_scroll_frame.grid_columnconfigure(0, weight=1)
        
        self.task_ui_elements = {} # uid -> widgets dict

    def refresh_tasks_ui(self):
        # Filter to only include active downloads/recordings
        active_download_tasks = {}
        for uid, task in self.active_tasks.items():
            status = task.get("status", "")
            if status in ["錄影中", "下載中", "準備錄影", "解析連線中...", "佇列中"]:
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
                lbl = ctk.CTkLabel(self.tasks_scroll_frame, text="目前沒有進行中的下載任務", text_color=self.c_text_secondary, font=ctk.CTkFont(size=14))
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
                row_frame.pack(fill="x", padx=5, pady=5)
                row_frame.grid_columnconfigure(2, weight=1)
                
                # Platform outline badge
                plat = task.get("platform", "Unknown")
                p_color, p_bg = self.platform_colors.get(plat, (self.c_text_secondary, self.c_card))
                plat_lbl = ctk.CTkLabel(
                    row_frame, 
                    text=plat.upper(), 
                    fg_color=p_bg,
                    text_color=p_color, 
                    corner_radius=4,
                    width=75,
                    height=22,
                    font=ctk.CTkFont(size=9, weight="bold")
                )
                plat_lbl.grid(row=0, column=0, padx=15, pady=15)
                
                # Task Channel Name
                name_lbl = ctk.CTkLabel(row_frame, text=task.get("channel_name", "Unknown"), font=ctk.CTkFont(weight="bold"), text_color=self.c_text_primary)
                name_lbl.grid(row=0, column=1, padx=10, pady=15, sticky="w")
                
                # Stats / Speed Label
                stats_lbl = ctk.CTkLabel(row_frame, text="佇列中...", text_color=self.c_text_secondary, font=ctk.CTkFont(size=12))
                stats_lbl.grid(row=0, column=2, padx=15, pady=15, sticky="e")
                
                # Progress Bar
                prog_bar = ctk.CTkProgressBar(row_frame, width=200, progress_color=self.c_accent)
                prog_bar.grid(row=0, column=3, padx=15, pady=15)
                prog_bar.set(0)
                
                # Kill Button
                kill_btn = ctk.CTkButton(
                    row_frame, 
                    text="✖ 停止", 
                    width=65, 
                    fg_color=self.c_red, 
                    hover_color="#db3b3b", 
                    font=ctk.CTkFont(weight="bold"),
                    command=lambda u=uid: self.kill_active_task(u)
                )
                kill_btn.grid(row=0, column=4, padx=15, pady=15)
                
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
                    proc.kill()
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
        header_frame.grid(row=0, column=0, padx=15, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="📜 已完成的歷史紀錄 (近500筆)", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        # Scroll Area
        self.hist_scroll_frame = ctk.CTkScrollableFrame(
            hist_frame, 
            fg_color=self.c_frame, 
            border_color=self.c_card_border, 
            border_width=1, 
            corner_radius=12
        )
        self.hist_scroll_frame.grid(row=1, column=0, padx=15, pady=5, sticky="nsew")
        self.hist_scroll_frame.grid_columnconfigure(2, weight=1)

    def refresh_history_ui(self):
        for widget in self.hist_scroll_frame.winfo_children():
            widget.destroy()
            
        if not self.history:
            ctk.CTkLabel(self.hist_scroll_frame, text="尚無已完成的下載歷史紀錄", text_color=self.c_text_secondary, font=ctk.CTkFont(size=14)).pack(pady=40)
            return
            
        for entry in self.history:
            row_frame = ctk.CTkFrame(
                self.hist_scroll_frame,
                fg_color=self.c_card,
                border_color=self.c_card_border,
                border_width=1,
                corner_radius=8
            )
            row_frame.pack(fill="x", padx=5, pady=5)
            row_frame.grid_columnconfigure(2, weight=1)
            
            # Platform Badge
            plat = entry.get("platform", "Unknown")
            p_color, p_bg = self.platform_colors.get(plat, (self.c_text_secondary, self.c_card))
            plat_badge = ctk.CTkLabel(
                row_frame, 
                text=plat.upper(), 
                fg_color=p_bg,
                text_color=p_color, 
                corner_radius=4,
                width=75,
                height=22,
                font=ctk.CTkFont(size=9, weight="bold")
            )
            plat_badge.grid(row=0, column=0, padx=15, pady=12)
            
            # Channel Details Stack
            left_info_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            left_info_frame.grid(row=0, column=1, padx=5, pady=5, sticky="w")
            
            chan_lbl = ctk.CTkLabel(left_info_frame, text=entry.get("channel", "Unknown"), font=ctk.CTkFont(size=13, weight="bold"), text_color=self.c_text_primary)
            chan_lbl.pack(anchor="w")
            time_lbl = ctk.CTkLabel(left_info_frame, text=entry.get("timestamp", ""), font=ctk.CTkFont(size=11), text_color=self.c_text_secondary)
            time_lbl.pack(anchor="w")
            
            # Video Title
            v_title = entry.get("title", "未命名標題")
            if len(v_title) > 60:
                v_title = v_title[:57] + "..."
            title_lbl = ctk.CTkLabel(row_frame, text=v_title, font=ctk.CTkFont(size=13), text_color=self.c_text_primary)
            title_lbl.grid(row=0, column=2, padx=15, pady=12, sticky="w")
            
            # Size Label
            size_lbl = ctk.CTkLabel(row_frame, text=entry.get("size", "Unknown"), font=ctk.CTkFont(size=12), text_color=self.c_text_secondary)
            size_lbl.grid(row=0, column=3, padx=15, pady=12)
            
            # Quick Actions Frame
            act_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            act_frame.grid(row=0, column=4, padx=15, pady=12, sticky="e")
            
            path = entry.get("file_path", "")
            
            open_btn = ctk.CTkButton(
                act_frame, 
                text="📂 資料夾", 
                width=65, 
                height=26, 
                fg_color="gray30", 
                hover_color="gray40", 
                text_color=self.c_text_primary,
                font=ctk.CTkFont(size=11, weight="bold"),
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
                font=ctk.CTkFont(size=11, weight="bold"),
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
        header_frame.grid(row=0, column=0, padx=15, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="🔗 指派單次影音手動下載任務", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        # Content frame
        content_box = ctk.CTkFrame(manual_frame, fg_color=self.c_frame, border_color=self.c_card_border, border_width=1, corner_radius=12)
        content_box.grid(row=1, column=0, padx=15, pady=5, sticky="nsew")
        content_box.grid_columnconfigure(1, weight=1)
        content_box.grid_rowconfigure(5, weight=1)
        
        form_label_font = ctk.CTkFont(size=13, weight="bold")
        
        # URL Field
        ctk.CTkLabel(content_box, text="影音網址 (URL):", text_color=self.c_text_secondary, font=form_label_font).grid(row=0, column=0, padx=25, pady=(25, 10), sticky="w")
        
        url_container = ctk.CTkFrame(content_box, fg_color="transparent")
        url_container.grid(row=0, column=1, columnspan=2, padx=25, pady=(25, 10), sticky="ew")
        url_container.grid_columnconfigure(0, weight=1)
        
        self.manual_url_entry = ctk.CTkEntry(
            url_container,
            placeholder_text="輸入單個直播、影片、YouTube 網址...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            text_color=self.c_text_primary,
            placeholder_text_color="#4f6668"
        )
        self.manual_url_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        
        load_txt_btn = ctk.CTkButton(
            url_container,
            text="📁 載入 TXT...",
            width=90,
            fg_color="gray30",
            hover_color="gray40",
            text_color=self.c_text_primary,
            font=ctk.CTkFont(weight="bold"),
            command=self.load_manual_urls_from_txt
        )
        load_txt_btn.grid(row=0, column=1, padx=0, pady=0)
        
        # Custom Subfolder Field
        ctk.CTkLabel(content_box, text="儲存資料夾名稱/路徑:", text_color=self.c_text_secondary, font=form_label_font).grid(row=1, column=0, padx=25, pady=10, sticky="w")
        
        subfolder_container = ctk.CTkFrame(content_box, fg_color="transparent")
        subfolder_container.grid(row=1, column=1, columnspan=2, padx=25, pady=10, sticky="ew")
        subfolder_container.grid_columnconfigure(0, weight=1)
        
        self.manual_name_entry = ctk.CTkEntry(
            subfolder_container,
            placeholder_text="例如: セラ (預設儲存資料夾名稱)，或瀏覽選取實體路徑...",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            text_color=self.c_text_primary,
            placeholder_text_color="#4f6668"
        )
        self.manual_name_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        
        browse_dir_btn = ctk.CTkButton(
            subfolder_container, 
            text="瀏覽...", 
            width=80, 
            fg_color="gray30", 
            hover_color="gray40", 
            text_color=self.c_text_primary,
            font=ctk.CTkFont(weight="bold"),
            command=self.browse_manual_download_directory
        )
        browse_dir_btn.grid(row=0, column=1, padx=0, pady=0)
        
        # Platform Selection Dropdown
        ctk.CTkLabel(content_box, text="指定平台:", text_color=self.c_text_secondary, font=form_label_font).grid(row=2, column=0, padx=25, pady=10, sticky="w")
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
            dropdown_hover_color=self.c_accent_hover
        )
        self.manual_plat_menu.grid(row=2, column=1, padx=25, pady=10, sticky="w")
        
        # Quality & Format Selection for Manual Download
        ctk.CTkLabel(content_box, text="畫質與格式選擇:", text_color=self.c_text_secondary, font=form_label_font).grid(row=3, column=0, padx=25, pady=10, sticky="w")
        
        qf_container = ctk.CTkFrame(content_box, fg_color="transparent")
        qf_container.grid(row=3, column=1, columnspan=2, padx=25, pady=10, sticky="ew")
        
        # Quality dropdown
        self.manual_quality_var = ctk.StringVar(value="best")
        self.manual_quality_menu = ctk.CTkOptionMenu(
            qf_container,
            variable=self.manual_quality_var,
            values=["best", "1080p", "720p", "480p", "360p", "worst"],
            width=100,
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover
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
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover
        )
        self.manual_format_menu.pack(side="left")
        
        # Trigger Actions Frame
        self.manual_dl_btn = ctk.CTkButton(
            content_box,
            text="📥 指派並開始下載任務",
            height=45,
            fg_color=self.c_green,
            hover_color="#0fa472",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.trigger_manual_download
        )
        self.manual_dl_btn.grid(row=4, column=0, columnspan=3, padx=25, pady=25, sticky="ew")

    def trigger_manual_download(self):
        url = self.manual_url_entry.get().strip()
        url = smart_redirect_url(url)
        self.manual_url_entry.delete(0, 'end')
        self.manual_url_entry.insert(0, url)
        
        custom_path = self.manual_name_entry.get().strip()
        
        if not url:
            self.add_log("手動下載失敗: 影音網址不能為空！", "WARNING")
            return
            
        if not custom_path:
            custom_path = "Manual_Downloads"
            
        plat = self.manual_platform_var.get()
        if plat == "自動偵測":
            plat = detect_platform(url)
            
        display_name = os.path.basename(custom_path) if (":" in custom_path or "/" in custom_path or "\\" in custom_path) else custom_path
        if not display_name:
            display_name = "Manual_Download"
            
        import random
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
        
        quality = self.manual_quality_var.get()
        fmt = self.manual_format_var.get()
        
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
        header_frame.grid(row=0, column=0, padx=15, pady=(10, 15), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(header_frame, text="⚙️ 系統及平台設定", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.c_text_primary).grid(row=0, column=0, sticky="w")
        
        self.save_settings_btn = ctk.CTkButton(
            header_frame, 
            text="💾 儲存所有設定", 
            fg_color=self.c_green, 
            hover_color="#0fa472", 
            font=ctk.CTkFont(weight="bold"),
            command=self.apply_and_save_settings_gui
        )
        self.save_settings_btn.grid(row=0, column=1, sticky="e")
        
        # Settings Fields Scrollable Frame
        scroll_settings = ctk.CTkScrollableFrame(settings_frame, fg_color=self.c_frame, border_color=self.c_card_border, border_width=1, corner_radius=12)
        scroll_settings.grid(row=1, column=0, padx=15, pady=5, sticky="nsew")
        scroll_settings.grid_columnconfigure(1, weight=1)
        
        form_label_font = ctk.CTkFont(size=13, weight="bold")
        header_font = ctk.CTkFont(size=15, weight="bold")
        
        row_idx = 0
        
        # ================= 🖥️ 系統運作設定 =================
        ctk.CTkLabel(scroll_settings, text="🖥️ 系統運作設定", font=header_font, text_color=self.c_text_primary).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")
        row_idx += 1
        
        # 預設檔案儲存路徑
        ctk.CTkLabel(scroll_settings, text="預設檔案儲存路徑:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        dir_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        dir_frame.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        dir_frame.grid_columnconfigure(0, weight=1)
        
        self.download_dir_entry = ctk.CTkEntry(dir_frame, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.download_dir_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.download_dir_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        dir_browse_btn = ctk.CTkButton(dir_frame, text="瀏覽...", width=70, fg_color="gray30", hover_color="gray40", text_color=self.c_text_primary, command=self.browse_download_directory)
        dir_browse_btn.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # Discord 通知 Webhook
        ctk.CTkLabel(scroll_settings, text="Discord 通知 Webhook:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.discord_webhook_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.discord_webhook_entry.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        self.discord_webhook_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # 手動排程下載上限
        ctk.CTkLabel(scroll_settings, text="手動排程下載上限:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.max_dl_spinner = ctk.CTkEntry(scroll_settings, width=80, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.max_dl_spinner.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        self.max_dl_spinner.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # 自訂連線 User-Agent
        ctk.CTkLabel(scroll_settings, text="自訂連線 User-Agent:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.ua_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.ua_entry.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        self.ua_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # 下載不休眠設定
        ctk.CTkLabel(scroll_settings, text="下載不休眠設定:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.prevent_sleep_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["下載時不休眠", "不休眠", "休眠"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_card_border,
            text_color=self.c_text_primary,
            command=self.on_settings_modified
        )
        self.prevent_sleep_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=2, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=15, sticky="ew")
        row_idx += 1
        
        # ================= 🎥 YouTube 下載設定 =================
        ctk.CTkLabel(scroll_settings, text="🎥 YouTube 下載設定", font=header_font, text_color=self.c_red).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")
        row_idx += 1
        
        # Cookies 檔案位置
        ctk.CTkLabel(scroll_settings, text="YouTube Cookies 檔案位置:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        cookies_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        cookies_frame.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        cookies_frame.grid_columnconfigure(0, weight=1)
        
        self.cookies_file_entry = ctk.CTkEntry(cookies_frame, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.cookies_file_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.cookies_file_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        cookies_browse_btn = ctk.CTkButton(cookies_frame, text="瀏覽...", width=70, fg_color="gray30", hover_color="gray40", text_color=self.c_text_primary, command=self.browse_cookies_file)
        cookies_browse_btn.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # YT 畫質優先度 (yt-dlp)
        ctk.CTkLabel(scroll_settings, text="YT 畫質優先度 (yt-dlp):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.yt_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["best", "1080p", "720p", "480p", "360p", "worst"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.yt_quality_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # YT 檔案格式 (yt-dlp)
        ctk.CTkLabel(scroll_settings, text="YT 檔案格式 (yt-dlp):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.yt_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "webm"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.yt_format_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # 指定關鍵字
        ctk.CTkLabel(scroll_settings, text="指定關鍵字:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.yt_keywords_entry = ctk.CTkEntry(
            scroll_settings, 
            placeholder_text="例如: 歌枠,歌回 (標題包含任一詞才下載，空白則全下載)",
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            text_color=self.c_text_primary,
            placeholder_text_color="#4f6668"
        )
        self.yt_keywords_entry.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        self.yt_keywords_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=2, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=15, sticky="ew")
        row_idx += 1
        
        # ================= 🎵 Rplay 平台設定 =================
        ctk.CTkLabel(scroll_settings, text="🎵 Rplay 平台設定", font=header_font, text_color=self.c_blue).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")
        row_idx += 1
        
        # Rplay Token
        ctk.CTkLabel(scroll_settings, text="Rplay Token:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        rplay_pwd_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        rplay_pwd_frame.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        rplay_pwd_frame.grid_columnconfigure(0, weight=1)
        
        self.rplay_token_entry = ctk.CTkEntry(rplay_pwd_frame, show="*", fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.rplay_token_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.rplay_token_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        self.btn_toggle_rplay_token = ctk.CTkButton(
            rplay_pwd_frame, text="👁️", width=36, fg_color="gray30", hover_color="gray40", text_color=self.c_text_primary,
            command=lambda: self.toggle_password_visibility(self.rplay_token_entry, self.btn_toggle_rplay_token)
        )
        self.btn_toggle_rplay_token.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1

        # Rplay Username
        ctk.CTkLabel(scroll_settings, text="Rplay 帳號 (Email):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.rplay_username_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.rplay_username_entry.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        self.rplay_username_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1

        # Rplay Password
        ctk.CTkLabel(scroll_settings, text="Rplay 密碼:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        rplay_pwd_frame2 = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        rplay_pwd_frame2.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        rplay_pwd_frame2.grid_columnconfigure(0, weight=1)
        
        self.rplay_password_entry = ctk.CTkEntry(rplay_pwd_frame2, show="*", fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.rplay_password_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.rplay_password_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        self.btn_toggle_rplay_password = ctk.CTkButton(
            rplay_pwd_frame2, text="👁️", width=36, fg_color="gray30", hover_color="gray40", text_color=self.c_text_primary,
            command=lambda: self.toggle_password_visibility(self.rplay_password_entry, self.btn_toggle_rplay_password)
        )
        self.btn_toggle_rplay_password.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # Rplay 畫質優先度
        ctk.CTkLabel(scroll_settings, text="Rplay 畫質優先度:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.rplay_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["best", "1080p", "720p", "480p", "360p", "worst"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.rplay_quality_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1

        # Rplay 檔案格式
        ctk.CTkLabel(scroll_settings, text="Rplay 檔案格式:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.rplay_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "ts"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.rplay_format_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=2, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=15, sticky="ew")
        row_idx += 1
        
        # ================= 🪐 Withny 平台設定 =================
        ctk.CTkLabel(scroll_settings, text="🪐 Withny 平台設定", font=header_font, text_color=self.c_purple).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")
        row_idx += 1
        
        # Withny Token
        ctk.CTkLabel(scroll_settings, text="Withny Token (Session):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        withny_pwd_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        withny_pwd_frame.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        withny_pwd_frame.grid_columnconfigure(0, weight=1)
        
        self.withny_token_entry = ctk.CTkEntry(withny_pwd_frame, show="*", fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.withny_token_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.withny_token_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        self.btn_toggle_withny_token = ctk.CTkButton(
            withny_pwd_frame, text="👁️", width=36, fg_color="gray30", hover_color="gray40", text_color=self.c_text_primary,
            command=lambda: self.toggle_password_visibility(self.withny_token_entry, self.btn_toggle_withny_token)
        )
        self.btn_toggle_withny_token.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # Withny 畫質優先度 (僅限原始畫質)
        ctk.CTkLabel(scroll_settings, text="Withny 畫質優先度:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.withny_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["自動(原始)"],
            fg_color=self.c_sidebar,
            button_color="gray30",
            button_hover_color="gray30",
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            state="disabled"
        )
        self.withny_quality_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # Withny 影音格式轉換 (Remux)
        ctk.CTkLabel(scroll_settings, text="Withny 影音格式轉換 (Remux):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.withny_remux_var = ctk.BooleanVar()
        self.withny_remux_switch = ctk.CTkSwitch(
            scroll_settings, text="啟用 TS 格式自動重封裝 (推薦)", variable=self.withny_remux_var,
            text_color=self.c_text_primary, progress_color=self.c_accent, button_color=self.c_text_primary, button_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.withny_remux_switch.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # Withny Remux 目標副檔名
        ctk.CTkLabel(scroll_settings, text="Withny Remux 目標副檔名:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.withny_remux_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "mov", "ts"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.withny_remux_format_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # Withny 合併連續分段 (Concat)
        ctk.CTkLabel(scroll_settings, text="Withny 合併連續分段 (Concat):", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.withny_concat_var = ctk.BooleanVar()
        self.withny_concat_switch = ctk.CTkSwitch(
            scroll_settings, text="自動合併多段 TS 串流檔", variable=self.withny_concat_var,
            text_color=self.c_text_primary, progress_color=self.c_accent, button_color=self.c_text_primary, button_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.withny_concat_switch.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # Withny 保留 TS 暫存分段
        ctk.CTkLabel(scroll_settings, text="Withny 保留 TS 暫存分段:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.withny_keep_var = ctk.BooleanVar()
        self.withny_keep_switch = ctk.CTkSwitch(
            scroll_settings, text="合併後保留原本的分段小 TS 檔案", variable=self.withny_keep_var,
            text_color=self.c_text_primary, progress_color=self.c_accent, button_color=self.c_text_primary, button_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.withny_keep_switch.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1
        
        # Withny 斷線重試檢查間隔
        ctk.CTkLabel(scroll_settings, text="Withny 斷線重試檢查間隔:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.withny_wait_entry = ctk.CTkEntry(scroll_settings, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.withny_wait_entry.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        self.withny_wait_entry.bind("<KeyRelease>", self.on_settings_modified)
        row_idx += 1
        
        # Divider
        ctk.CTkFrame(scroll_settings, height=2, fg_color=self.c_card_border).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=15, sticky="ew")
        row_idx += 1
        
        # ================= 📺 FC2 平台設定 =================
        ctk.CTkLabel(scroll_settings, text="📺 FC2 平台設定", font=header_font, text_color=self.c_yellow).grid(row=row_idx, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")
        row_idx += 1
        
        # FC2 Cookies 檔案位置
        ctk.CTkLabel(scroll_settings, text="FC2 Cookies 檔案位置:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        fc2_cookies_frame = ctk.CTkFrame(scroll_settings, fg_color="transparent")
        fc2_cookies_frame.grid(row=row_idx, column=1, padx=25, pady=8, sticky="ew")
        fc2_cookies_frame.grid_columnconfigure(0, weight=1)
        
        self.fc2_cookies_file_entry = ctk.CTkEntry(fc2_cookies_frame, fg_color=self.c_sidebar, border_color=self.c_card_border, text_color=self.c_text_primary)
        self.fc2_cookies_file_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.fc2_cookies_file_entry.bind("<KeyRelease>", self.on_settings_modified)
        
        fc2_cookies_browse_btn = ctk.CTkButton(fc2_cookies_frame, text="瀏覽...", width=70, fg_color="gray30", hover_color="gray40", text_color=self.c_text_primary, command=self.browse_fc2_cookies_file)
        fc2_cookies_browse_btn.grid(row=0, column=1, padx=0, pady=0)
        row_idx += 1
        
        # FC2 畫質優先度
        ctk.CTkLabel(scroll_settings, text="FC2 畫質優先度:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.fc2_quality_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["3Mbps", "2Mbps", "1.2Mbps", "400Kbps", "150Kbps", "sound"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.fc2_quality_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
        row_idx += 1

        # FC2 檔案格式
        ctk.CTkLabel(scroll_settings, text="FC2 檔案格式:", text_color=self.c_text_secondary, font=form_label_font).grid(row=row_idx, column=0, padx=25, pady=8, sticky="w")
        self.fc2_format_menu = ctk.CTkOptionMenu(
            scroll_settings,
            values=["mp4", "mkv", "ts"],
            fg_color=self.c_sidebar,
            button_color=self.c_accent,
            button_hover_color=self.c_accent_hover,
            dropdown_fg_color=self.c_sidebar,
            dropdown_text_color=self.c_text_primary,
            dropdown_hover_color=self.c_accent_hover,
            command=self.on_settings_modified
        )
        self.fc2_format_menu.grid(row=row_idx, column=1, padx=25, pady=8, sticky="w")
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
        filepath = ctk.filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not filepath:
            return
            
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e:
            self.add_log(f"讀取 TXT 檔案失敗: {e}", "ERROR")
            messagebox.showerror("錯誤", f"讀取 TXT 檔案失敗: {e}")
            return
            
        urls = []
        for line in lines:
            line = line.strip()
            if line:
                match = re.search(r'(https?://[^\s]+)', line)
                if match:
                    urls.append(match.group(1))
                    
        if not urls:
            messagebox.showwarning("警告", "在選取的檔案中沒有找到任何有效網址！")
            return
            
        custom_path = self.manual_name_entry.get().strip()
        if not custom_path:
            custom_path = "Manual_Downloads"
            
        import random
        quality = self.manual_quality_var.get()
        fmt = self.manual_format_var.get()
        triggered_count = 0
        for url in urls:
            url = smart_redirect_url(url)
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
                "status": "佇列中",
                "progress": 0.0,
                "speed": "",
                "size": "",
                "elapsed": "00:00",
                "start_time": time.time(),
                "process": None
            }
            
            self.add_log(f"已手動指派下載任務: {url}", "INFO")
            t = threading.Thread(target=workers.worker_manual_download, args=(self, uid, url, custom_path, plat, quality, fmt), daemon=True)
            t.start()
            triggered_count += 1
            
        self.refresh_tasks_ui()
        messagebox.showinfo("成功", f"已成功指派 {triggered_count} 個下載任務！")
        self.select_tab("tasks")

    def apply_and_save_settings_gui(self):
        self.settings["rplay_token"] = self.rplay_token_entry.get().strip()
        self.settings["rplay_username"] = self.rplay_username_entry.get().strip()
        self.settings["rplay_password"] = self.rplay_password_entry.get().strip()
        self.settings["withny_token"] = self.withny_token_entry.get().strip()
        self.settings["download_dir"] = self.download_dir_entry.get().strip().replace("\\", "/")
        self.settings["cookies_file"] = self.cookies_file_entry.get().strip().replace("\\", "/")
        self.settings["fc2_cookies_file"] = self.fc2_cookies_file_entry.get().strip().replace("\\", "/")
        self.settings["discord_webhook"] = self.discord_webhook_entry.get().strip()
        self.settings["prevent_sleep_mode"] = self.prevent_sleep_menu.get()
        
        try:
            self.settings["max_concurrent_downloads"] = int(self.max_dl_spinner.get().strip())
        except:
            self.settings["max_concurrent_downloads"] = 2
            
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
            messagebox.showinfo("💾 儲存成功", "設定已成功儲存並同步！")

    def on_settings_modified(self, *args):
        self.settings_dirty = True

    def reset_settings_fields_from_state(self):
        self.rplay_token_entry.delete(0, "end")
        self.rplay_token_entry.insert(0, self.settings.get("rplay_token", ""))
        
        self.rplay_username_entry.delete(0, "end")
        self.rplay_username_entry.insert(0, self.settings.get("rplay_username", ""))
        
        self.rplay_password_entry.delete(0, "end")
        self.rplay_password_entry.insert(0, self.settings.get("rplay_password", ""))
        
        self.withny_token_entry.delete(0, "end")
        self.withny_token_entry.insert(0, self.settings.get("withny_token", ""))
        
        self.download_dir_entry.delete(0, "end")
        self.download_dir_entry.insert(0, self.settings.get("download_dir", ""))
        
        self.cookies_file_entry.delete(0, "end")
        self.cookies_file_entry.insert(0, self.settings.get("cookies_file", ""))
        
        self.fc2_cookies_file_entry.delete(0, "end")
        self.fc2_cookies_file_entry.insert(0, self.settings.get("fc2_cookies_file", ""))
        
        self.discord_webhook_entry.delete(0, "end")
        self.discord_webhook_entry.insert(0, self.settings.get("discord_webhook", ""))
        
        self.max_dl_spinner.delete(0, "end")
        self.max_dl_spinner.insert(0, str(self.settings.get("max_concurrent_downloads", 2)))
        
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
        
        self.prevent_sleep_menu.set(self.settings.get("prevent_sleep_mode", "下載時不休眠"))

    # ================= Logs Tab =================
    def build_logs_tab(self):
        logs_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.frames["logs"] = logs_frame
        logs_frame.grid_columnconfigure(0, weight=1)
        logs_frame.grid_rowconfigure(1, weight=1)
        
        # Header
        header_frame = ctk.CTkFrame(logs_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=15, pady=(10, 15), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(header_frame, text="📝 系統日誌與連線狀態", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.c_text_primary).grid(row=0, column=0, sticky="w")
        
        clear_btn = ctk.CTkButton(header_frame, text="🧹 清空日誌", fg_color="gray30", hover_color="gray40", text_color=self.c_text_primary, font=ctk.CTkFont(weight="bold"), command=self.clear_logs)
        clear_btn.grid(row=0, column=1, sticky="e")
        
        # Consolas console terminal log window
        self.log_textbox = ctk.CTkTextbox(
            logs_frame, 
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=self.c_sidebar,
            border_color=self.c_card_border,
            border_width=1,
            text_color=self.c_text_primary
        )
        self.log_textbox.grid(row=1, column=0, padx=15, pady=5, sticky="nsew")
        self.log_textbox.configure(state="disabled")
        
        # Setup specific console colors matching log levels
        self.log_textbox.tag_config("SUCCESS", foreground=self.c_green)
        self.log_textbox.tag_config("WARNING", foreground=self.c_yellow)
        self.log_textbox.tag_config("ERROR", foreground=self.c_red)
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
        header_frame.grid(row=0, column=0, padx=15, pady=(10, 15), sticky="ew")
        
        ctk.CTkLabel(header_frame, text="🔄 系統元件與依賴更新", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.c_text_primary).pack(side="left")
        
        self.btn_check_updates = ctk.CTkButton(
            header_frame, 
            text="🔍 一鍵檢查所有更新", 
            fg_color=self.c_accent, 
            hover_color=self.c_accent_hover, 
            text_color=self.c_text_primary,
            font=ctk.CTkFont(weight="bold"),
            command=self.check_all_updates_async
        )
        self.btn_check_updates.pack(side="right")
        
        # Component Grid / Scroll Frame
        scroll_updates = ctk.CTkScrollableFrame(updates_frame, fg_color=self.c_frame, border_color=self.c_card_border, border_width=1, corner_radius=12, height=320)
        scroll_updates.grid(row=1, column=0, padx=15, pady=5, sticky="ew")
        scroll_updates.grid_columnconfigure((0, 1), weight=1)
        
        # Components Data List
        self.comp_ui = {}
        
        components = [
            ("ytdlp", "🔴 yt-dlp 下載核心", lambda: updater.get_local_version_ytdlp(self), self.update_ytdlp_async),
            ("ffmpeg", "🎞️ FFmpeg 解碼器", lambda: updater.get_local_version_ffmpeg(self), self.update_ffmpeg_async),
            ("rplay", "🔗 Rplay 提取擴充", lambda: updater.get_local_version_rplay(self), self.update_rplay_async),
            ("withnydl", "🟣 Withny-dl 監控器", lambda: updater.get_local_version_withnydl(self), self.update_withnydl_async)
        ]
        
        form_label_font = ctk.CTkFont(size=13, weight="bold")
        
        for idx, (cid, name, loc_func, update_func) in enumerate(components):
            card = ctk.CTkFrame(
                scroll_updates, 
                fg_color=self.c_card, 
                border_color=self.c_card_border, 
                border_width=1, 
                corner_radius=12
            )
            r = idx // 2
            c = idx % 2
            card.grid(row=r, column=c, padx=10, pady=10, sticky="nsew")
            card.grid_columnconfigure(1, weight=1)
            
            # Content layout
            ctk.CTkLabel(card, text=name, font=ctk.CTkFont(size=14, weight="bold"), text_color=self.c_text_primary).grid(row=0, column=0, columnspan=3, padx=15, pady=(12, 6), sticky="w")
            
            # Local Version Info
            ctk.CTkLabel(card, text="本機版本:", font=form_label_font, text_color=self.c_text_secondary).grid(row=1, column=0, padx=15, pady=3, sticky="w")
            lbl_loc = ctk.CTkLabel(card, text="檢測中...", text_color=self.c_text_primary)
            lbl_loc.grid(row=1, column=1, padx=5, pady=3, sticky="w")
            
            # Online Version Info
            ctk.CTkLabel(card, text="最新線上:", font=form_label_font, text_color=self.c_text_secondary).grid(row=2, column=0, padx=15, pady=3, sticky="w")
            
            lbl_online = ctk.CTkLabel(card, text="未檢查", text_color=self.c_text_secondary)
            lbl_online.grid(row=2, column=1, padx=5, pady=3, sticky="w")
            if cid == "ytdlp":
                self.lbl_online_ytdlp = lbl_online
            elif cid == "ffmpeg":
                self.lbl_online_ffmpeg = lbl_online
            elif cid == "rplay":
                self.lbl_online_rplay = lbl_online
            elif cid == "withnydl":
                self.lbl_online_withnydl = lbl_online
                
            # Status Badge indicator
            lbl_status = ctk.CTkLabel(card, text="未檢查 ⚪", text_color=self.c_text_secondary, font=ctk.CTkFont(weight="bold"))
            lbl_status.grid(row=1, column=2, rowspan=2, padx=15, pady=3, sticky="e")
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
                font=ctk.CTkFont(weight="bold"),
                height=30,
                command=update_func
            )
            btn_update.grid(row=3, column=0, columnspan=3, padx=15, pady=(8, 15), sticky="ew")
            
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
            font=ctk.CTkFont(family="Consolas", size=12),
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
        self.updates_log("🔔 開始更新 Rplay Extractor...\n")
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
                        
                    # Rplay Extractor
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
            except queue.Empty:
                break
                
        # Update sleep prevention state dynamically
        self.update_sleep_prevention_state()
                
        # Schedule next poll
        self.after(100, self.poll_gui_updates)

    # ================= Sleep Prevention & Cleanups =================
    def update_sleep_prevention_state(self):
        mode = self.settings.get("prevent_sleep_mode", "下載時不休眠")
        
        has_active_downloads = False
        if mode == "下載時不休眠":
            for task in self.active_tasks.values():
                if task.get("process") and task["process"].poll() is None:
                    has_active_downloads = True
                    break
        
        target_prevent = False
        if mode == "不休眠":
            target_prevent = True
        elif mode == "下載時不休眠":
            target_prevent = has_active_downloads
        else: # "休眠"
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
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
                self.add_log("防睡眠機制已啟用 (阻斷電腦自動休眠)", "SUCCESS")
            except Exception as e:
                self.add_log(f"防睡眠機制啟用失敗: {e}", "WARNING")

    def apply_restore_sleep(self):
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
                self.add_log("防睡眠機制已解除 (恢復系統休眠設定)", "INFO")
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
    def send_discord_notify(self, url, title, custom_name, platform="直播", include_url=True, image_url=None):
        webhook = self.settings["discord_webhook"]
        if not webhook or "http" not in webhook:
            return
        content = f"🚨 **{title}**\n名稱: {custom_name}"
        if include_url:
            content += f"\n網址: {url}"
        payload = {"username": f"{platform} 監控小幫手", "content": content}
        if image_url:
            payload["embeds"] = [{"image": {"url": image_url}}]
            
        def _send():
            try:
                import requests
                requests.post(webhook, json=payload, timeout=10)
            except Exception as e:
                print(f"Discord notification failed: {e}")
                
        threading.Thread(target=_send, daemon=True).start()

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
            
        self.is_monitoring = True
        self.monitor_btn.configure(text="🛑 停止監控", fg_color=self.c_red, hover_color="#db3b3b")
        self.add_log("系統開始監控...", "INFO")
        
        clean_path, count = self.prepare_clean_cookies()
        if clean_path:
            self.add_log(f"已淨化並載入 Cookie ({count} 筆)", "SUCCESS")
            
        self.monitor_threads = []
        withny_record_targets = {}
        
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
                if channel["record"]:
                    cid = workers.extract_withny_id(channel["url"])
                    withny_record_targets[cid] = target
                else:
                    th = threading.Thread(target=workers.worker_withny_notify, args=(self, target), daemon=True)
            elif "live.fc2.com" in url_lower:
                th = threading.Thread(target=workers.worker_fc2, args=(self, target), daemon=True)
                
            if th:
                th.start()
                self.monitor_threads.append(th)
                
        if withny_record_targets:
            mth = threading.Thread(target=workers.worker_withny_master, args=(self, withny_record_targets), daemon=True)
            mth.start()
            self.monitor_threads.append(mth)
            
        self.gui_update_queue.put(("refresh_channels_list", None))

    def stop_monitoring(self):
        self.is_monitoring = False
        self.monitor_btn.configure(text="▶️ 啟動監控", fg_color=self.c_accent, hover_color=self.c_accent_hover)
        self.add_log("停止監控中，正在重置連線與子程序...", "WARNING")
        
        self.cleanup_temp_files()
        
        self.active_tasks.clear()
        self.gui_update_queue.put(("refresh_tasks", None))
        self.gui_update_queue.put(("refresh_channels_list", None))

    def check_rplay_status(self, url):
        import yt_dlp
        ydl_opts = {
            'extract_flat': True,
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
        }
        if self.settings.get("rplay_username") and self.settings.get("rplay_password"):
            ydl_opts['username'] = self.settings["rplay_username"]
            ydl_opts['password'] = self.settings["rplay_password"]
        else:
            ydl_opts['extractor_args'] = {'rplaylive': {'jwt_token': [self.settings.get("rplay_token", "")]}}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(url, download=False)
                if data and data.get('live_status') == 'is_live':
                    return data
        except:
            pass
        return None

    def find_newest_downloaded_file_in_dir(self, output_dir, start_time):
        if not os.path.exists(output_dir):
            return None
            
        newest_file = None
        newest_mtime = 0
        
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(file_path)
                    if mtime > start_time and mtime > newest_mtime:
                        # Exclude temporary intermediate TS files
                        if not file.endswith(".part") and not file.endswith(".ytdl") and not file.endswith(".ts"):
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
