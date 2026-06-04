import os
import re
import json
from datetime import datetime
from config import CONFIG_FILE, SETTINGS_FILE, CREDENTIALS_FILE, HISTORY_FILE, BASE_DIR

def load_all_configs(app):
    # Settings
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    app.settings[k] = v
        except:
            pass
    app.settings["download_dir"] = app.settings["download_dir"].replace("\\", "/")
    
    # Channels
    load_channels(app)
    
    # History
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                app.history = json.load(f)
        except:
            app.history = []

def save_settings(app):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(app.settings, f, indent=4, ensure_ascii=False)
        sync_withny_credentials(app)
        app.add_log("設定已成功儲存！", "SUCCESS")
        return True
    except Exception as e:
        app.add_log(f"儲存設定失敗: {e}", "ERROR")
        return False

def sync_withny_credentials(app):
    try:
        with open(os.path.join(BASE_DIR, CREDENTIALS_FILE), "w", encoding="utf-8") as f:
            f.write(f"sessionToken: '{app.settings['withny_token']}'\n")
    except Exception as e:
        app.add_log(f"同步 credentials.yaml 失敗: {e}", "ERROR")

def load_channels(app):
    json_file = os.path.join(BASE_DIR, CONFIG_FILE)
    txt_file = os.path.join(BASE_DIR, "mychannels.txt")
    old_txt_file = os.path.join(BASE_DIR, "channels.txt")
    
    # 1. Try to load from json first
    if os.path.exists(json_file):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                app.channels = json.load(f)
            app.channels_dirty = False
            return
        except Exception as e:
            print(f"Error loading channels.json: {e}")
            
    # 2. Migration from mychannels.txt or channels.txt
    migrate_path = None
    if os.path.exists(txt_file):
        migrate_path = txt_file
    elif os.path.exists(old_txt_file):
        migrate_path = old_txt_file
        
    if migrate_path:
        try:
            with open(migrate_path, 'r', encoding='utf-8') as f:
                content = f.read()
            pattern = re.compile(
                r'ID\s*:\s*"(?P<id>[^"]+)"\s*site.*?:\s*"(?P<site>[^"]+)"\s*archive.*?:\s*(?P<archive>True|False)(\s*image.*?:\s*"(?P<image>[^"]+)")?',
                re.IGNORECASE | re.DOTALL
            )
            app.channels = []
            for match in pattern.finditer(content):
                app.channels.append({
                    "name": match.group("id"),
                    "url": match.group("site"),
                    "record": match.group("archive").lower() == "true",
                    "image": match.group("image") or ""
                })
            # Save to JSON
            write_channels_file(app)
            
            # Rename the txt file to avoid re-migration
            try:
                os.rename(migrate_path, migrate_path + ".bak")
            except:
                pass
            app.channels_dirty = False
            return
        except Exception as e:
            print(f"Error migrating {migrate_path}: {e}")
            
    # Fallback
    app.channels = []
    app.channels_dirty = False

def write_channels_file(app):
    json_file = os.path.join(BASE_DIR, CONFIG_FILE)
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(app.channels, f, indent=4, ensure_ascii=False)
        app.add_log(f"頻道已成功寫入 {CONFIG_FILE}", "SUCCESS")
        app.channels_dirty = False
        return True
    except Exception as e:
        app.add_log(f"寫入頻道檔案失敗: {e}", "ERROR")
        return False

def add_history_entry(app, channel_name, platform, title, file_path):
    size_str = "Unknown"
    if file_path and os.path.exists(file_path):
        try:
            bytes_size = os.path.getsize(file_path)
            if bytes_size >= 1024**3:
                size_str = f"{bytes_size / (1024**3):.2f} GB"
            elif bytes_size >= 1024**2:
                size_str = f"{bytes_size / (1024**2):.2f} MB"
            else:
                size_str = f"{bytes_size / 1024:.2f} KB"
        except:
            pass
    
    entry = {
        "channel": channel_name,
        "platform": platform,
        "title": title,
        "file_path": file_path,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "size": size_str
    }
    
    app.history.insert(0, entry)
    app.history = app.history[:500] # Limit size
    
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(app.history, f, indent=4, ensure_ascii=False)
        app.gui_update_queue.put(("refresh_history", None))
    except Exception as e:
        print(f"Error saving history: {e}")
