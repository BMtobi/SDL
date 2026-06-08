import os
import sys
import re
import importlib
from config import BASE_DIR

class LazyModule:
    def __init__(self, name):
        self.__dict__["_name"] = name
        self.__dict__["_module"] = None

    def _load(self):
        if self.__dict__["_module"] is None:
            self.__dict__["_module"] = importlib.import_module(self.__dict__["_name"])
        return self.__dict__["_module"]

    def __getattr__(self, item):
        return getattr(self._load(), item)

    def __setattr__(self, key, value):
        setattr(self._load(), key, value)

    def __dir__(self):
        return dir(self._load())

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(value):
    return ANSI_ESCAPE.sub('', value)

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()

def detect_platform(url):
    url = url.lower()
    if "rplay.live" in url:
        return "Rplay"
    elif "withny.fun" in url:
        return "Withny"
    elif "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    elif "live.fc2.com" in url:
        return "FC2"
    return "Unknown"

def smart_redirect_url(url):
    if not url:
        return url
    
    url_clean = url.strip()
    
    # 1. Rplay
    # creatorhome/ID -> live/ID
    if "rplay.live/creatorhome/" in url_clean:
        url_clean = url_clean.replace("/creatorhome/", "/live/")
        if "?" in url_clean:
            url_clean = url_clean.split("?")[0]
            
    # c/customUrl -> c/customUrl/live (if it doesn't already end with /live or /play/ or contain /play/)
    elif "rplay.live/c/" in url_clean and not url_clean.endswith("/live") and "/play/" not in url_clean:
        base_part = url_clean.split("?")[0]
        if not base_part.endswith("/live"):
            if "?" in url_clean:
                parts = url_clean.split("?")
                url_clean = parts[0].rstrip("/") + "/live?" + parts[1]
            else:
                url_clean = url_clean.rstrip("/") + "/live"
                
    # 2. Withny
    # user/profile/username -> channels/username
    elif "withny.fun/user/profile/" in url_clean:
        url_clean = url_clean.replace("/user/profile/", "/channels/")
        
    # 3. YouTube
    # youtube.com/@handle or youtube.com/channel/UC... -> append /live
    elif "youtube.com" in url_clean or "youtu.be" in url_clean:
        is_channel = False
        if "/@" in url_clean:
            is_channel = True
        elif "/channel/" in url_clean:
            is_channel = True
        elif "/c/" in url_clean:
            is_channel = True
        elif "/user/" in url_clean:
            is_channel = True
            
        if is_channel:
            base_part = url_clean.split("?")[0].rstrip("/")
            if not base_part.endswith("/live"):
                if "?" in url_clean:
                    parts = url_clean.split("?")
                    url_clean = parts[0].rstrip("/") + "/live?" + parts[1]
                else:
                    url_clean = url_clean.rstrip("/") + "/live"
                    
    return url_clean

def get_binary_path(name):
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, name).replace("\\", "/")
        if os.path.exists(p):
            return p
    p = os.path.join(BASE_DIR, name).replace("\\", "/")
    if os.path.exists(p):
        return p
    return name

def kill_process_tree(proc):
    if not proc:
        return
    import subprocess
    if sys.platform == "win32":
        try:
            # Use taskkill to kill the process tree (/T) forcefully (/F) by PID
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            print(f"Failed to kill process tree: {e}")
            try:
                proc.kill()
            except:
                pass
    else:
        try:
            proc.kill()
        except:
            pass

def clean_dir_leftovers(download_dir):
    if not download_dir or not os.path.exists(download_dir):
        return
    for root, _, files in os.walk(download_dir):
        for file in files:
            file_path = os.path.join(root, file).replace("\\", "/")
            try:
                if file.endswith(".temp.mp4"):
                    base = file_path[:-9]
                    if os.path.exists(base + ".mp4") or os.path.exists(base + ".mkv") or os.path.exists(base + ".ts"):
                        os.remove(file_path)
                elif file.endswith(".temp.mkv"):
                    base = file_path[:-9]
                    if os.path.exists(base + ".mp4") or os.path.exists(base + ".mkv") or os.path.exists(base + ".ts"):
                        os.remove(file_path)
                elif file.endswith(".part"):
                    if file.endswith(".mp4.part"):
                        base_mp4 = file_path[:-5]
                        if os.path.exists(base_mp4):
                            os.remove(file_path)
                    elif file.endswith(".mkv.part"):
                        base_mkv = file_path[:-5]
                        if os.path.exists(base_mkv):
                            os.remove(file_path)
                    else:
                        base = file_path[:-5]
                        if os.path.exists(base + ".mp4") or os.path.exists(base + ".mkv") or os.path.exists(base + ".ts"):
                            os.remove(file_path)
                elif file.endswith(".ytdl"):
                    if file.endswith(".mp4.ytdl"):
                        base_mp4 = file_path[:-5]
                        if os.path.exists(base_mp4):
                            os.remove(file_path)
                    elif file.endswith(".mkv.ytdl"):
                        base_mkv = file_path[:-5]
                        if os.path.exists(base_mkv):
                            os.remove(file_path)
                    else:
                        base = file_path[:-5]
                        if os.path.exists(base + ".mp4") or os.path.exists(base + ".mkv") or os.path.exists(base + ".ts"):
                            os.remove(file_path)
                elif file.endswith(".ts"):
                    base = file_path[:-3]
                    if os.path.exists(base + ".mp4") or os.path.exists(base + ".mkv"):
                        os.remove(file_path)
            except Exception as e:
                print(f"Error cleaning file {file_path}: {e}")

