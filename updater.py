import os
import sys
import re
import shutil
import zipfile
import subprocess
from config import BASE_DIR
from utils import get_binary_path
import time



def get_local_version_ytdlp(app):
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        return "未偵測到"

def get_local_version_ffmpeg(app):
    try:
        exe_path = get_binary_path("ffmpeg.exe")
        res = subprocess.run([exe_path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        first_line = res.stdout.split('\n')[0]
        m = re.search(r'ffmpeg version (\S+)', first_line)
        if m:
            return m.group(1)
        return "已安裝"
    except Exception:
        return "未偵測到"
def get_local_version_rplay(app):
    return "原生整合 (v2.4-native)"

def get_local_version_withnydl(app):
    return "原生整合 (v2.0-native)"

def worker_check_updates(app):
    import requests
    def fetch_url_json(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.json()

    def fetch_url_text(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.text.strip()

    # 1. Check yt-dlp
    latest_ytdlp = "未知"
    try:
        url = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
        info = fetch_url_json(url)
        latest_ytdlp = info.get("tag_name", "未知")
        app.updates_log(f"   [yt-dlp] 最新線上版本: {latest_ytdlp}\n")
    except Exception as e:
        app.updates_log(f"❌ 檢查 [yt-dlp] 失敗: {e}\n")
        
    # 2. Check FFmpeg
    latest_ffmpeg = "未知"
    try:
        url = "https://www.gyan.dev/ffmpeg/builds/release-version"
        latest_ffmpeg = fetch_url_text(url)
        app.updates_log(f"   [FFmpeg] 最新線上版本: {latest_ffmpeg}\n")
    except Exception as e:
        app.updates_log(f"❌ 檢查 [FFmpeg] 失敗: {e}\n")
        
    # 3. Check Rplay Downloader (Native integrated)
    latest_rplay = "原生整合 (最新)"
    app.updates_log("   [Rplay 原生核心] 原生整合於 yt-dlp 核心中 (最新)\n")
        
    # 4. Check Withny Downloader (Native integrated)
    latest_withnydl = "原生整合 (最新)"
    app.updates_log("   [Withny 原生核心] 原生整合於 yt-dlp 核心中 (最新)\n")
        
    app.gui_update_queue.put(("check_updates_done", (latest_ytdlp, latest_ffmpeg, latest_rplay, latest_withnydl)))

def worker_update_ytdlp(app):
    import requests
    def fetch_url_json(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.json()

    try:
        url = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
        info = fetch_url_json(url)
        tag_name = info.get("tag_name")
        if not tag_name:
            raise Exception("無法取得最新版本標籤")
            
        temp_dir = os.path.join(BASE_DIR, "temp_ytdlp_update")
        os.makedirs(temp_dir, exist_ok=True)
        zip_url = f"https://github.com/yt-dlp/yt-dlp/archive/refs/tags/{tag_name}.zip"
        zip_path = os.path.join(temp_dir, "ytdlp.zip")
        
        # 1. Backup custom native extractors (rplaylive.py, withny.py)
        custom_extractors = {}
        for ext_name in ["rplaylive.py", "withny.py"]:
            ext_path = os.path.join(BASE_DIR, "yt_dlp", "extractor", ext_name)
            if os.path.exists(ext_path):
                with open(ext_path, "rb") as f:
                    custom_extractors[ext_name] = f.read()
        
        app.updates_log(f"📥 下載 yt-dlp 原始碼 ({tag_name})...\n")
        r = requests.get(zip_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        r.raise_for_status()
        with open(zip_path, 'wb') as f:
            f.write(r.content)
        
        app.updates_log("📂 解壓縮與保留 Rplay / Withny 原生提取器...\n")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            root_prefix = f"yt-dlp-{tag_name}/yt_dlp/"
            extracted_yt_dlp = os.path.join(temp_dir, "extracted_yt_dlp")
            os.makedirs(extracted_yt_dlp, exist_ok=True)
            
            for member in zip_ref.namelist():
                if member.startswith(root_prefix):
                    rel_path = member[len(root_prefix):]
                    if not rel_path:
                        continue
                    target_path = os.path.join(extracted_yt_dlp, rel_path).replace("\\", "/")
                    if member.endswith('/'):
                        os.makedirs(target_path, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with zip_ref.open(member) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
                            
        # 2. Re-inject custom extractors into the new extracted extractor directory
        new_extractor_dir = os.path.join(temp_dir, "extracted_yt_dlp", "extractor")
        os.makedirs(new_extractor_dir, exist_ok=True)
        for ext_name, content_bytes in custom_extractors.items():
            with open(os.path.join(new_extractor_dir, ext_name), "wb") as f:
                f.write(content_bytes)
                
        # 3. Ensure custom extractors are imported in new _extractors.py
        ext_init_path = os.path.join(new_extractor_dir, "_extractors.py")
        if os.path.exists(ext_init_path):
            with open(ext_init_path, "r", encoding="utf-8") as f:
                init_code = f.read()
            additions = []
            if "from .rplaylive import (" not in init_code:
                additions.append("\nfrom .rplaylive import (\n    RPlayIE,\n    RPlayLiveIE,\n    RPlayTagIE,\n    RPlayUserIE,\n)\n")
            if "from .withny import (" not in init_code:
                additions.append("\nfrom .withny import (\n    WithnyLiveIE,\n    WithnyPurchaseListIE,\n    WithnyVideoIE,\n)\n")
            if additions:
                with open(ext_init_path, "w", encoding="utf-8") as f:
                    f.write(init_code + "".join(additions))
                    
        local_yt_dlp_dir = os.path.join(BASE_DIR, "yt_dlp")
        if os.path.exists(local_yt_dlp_dir):
            shutil.rmtree(local_yt_dlp_dir)
        shutil.copytree(os.path.join(temp_dir, "extracted_yt_dlp"), local_yt_dlp_dir)
        
        shutil.rmtree(temp_dir, ignore_errors=True)
        app.updates_log("✅ yt-dlp 更新成功 (已自動同步並保留 Rplay & Withny 原生核心)！\n")
        app.gui_update_queue.put(("update_done", ("ytdlp", tag_name)))
    except Exception as e:
        app.updates_log(f"❌ yt-dlp 更新失敗: {e}\n")
        app.gui_update_queue.put(("update_failed", "ytdlp"))

def worker_update_ffmpeg(app):
    import requests
    def fetch_url_text(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.text.strip()

    try:
        url = "https://www.gyan.dev/ffmpeg/builds/release-version"
        latest_version = fetch_url_text(url)
        if not latest_version:
            raise Exception("無法取得最新版本資訊")
            
        temp_dir = os.path.join(BASE_DIR, "temp_ffmpeg_update")
        os.makedirs(temp_dir, exist_ok=True)
        
        zip_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = os.path.join(temp_dir, "ffmpeg.zip")
        app.updates_log(f"📥 下載 FFmpeg Release Build (版本 {latest_version})...\n")
        r = requests.get(zip_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        r.raise_for_status()
        with open(zip_path, 'wb') as f:
            f.write(r.content)
        
        app.updates_log("📂 解壓縮並覆蓋 ffmpeg.exe...\n")
        target_exe = os.path.join(BASE_DIR, "ffmpeg.exe")
        
        if os.path.exists(target_exe):
            try:
                bak_path = target_exe + ".bak"
                if os.path.exists(bak_path):
                    os.remove(bak_path)
                os.rename(target_exe, bak_path)
            except Exception as e:
                app.updates_log(f"⚠️ 無法重命名舊的 ffmpeg.exe: {e}，將嘗試直接覆寫\n")
                
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            ffmpeg_exe_member = None
            for member in zip_ref.namelist():
                if member.endswith("/bin/ffmpeg.exe") or member.endswith("\\bin\\ffmpeg.exe"):
                    ffmpeg_exe_member = member
                    break
            if not ffmpeg_exe_member:
                for member in zip_ref.namelist():
                    if member.endswith("ffmpeg.exe"):
                        ffmpeg_exe_member = member
                        break
                        
            if not ffmpeg_exe_member:
                raise Exception("在 Zip 壓縮檔中找不到 ffmpeg.exe")
                
            with zip_ref.open(ffmpeg_exe_member) as source, open(target_exe, "wb") as target:
                shutil.copyfileobj(source, target)
                
        bak_path = target_exe + ".bak"
        if os.path.exists(bak_path):
            try:
                os.remove(bak_path)
            except:
                pass
                
        shutil.rmtree(temp_dir, ignore_errors=True)
        app.updates_log("✅ FFmpeg 更新成功！\n")
        app.gui_update_queue.put(("update_done", ("ffmpeg", latest_version)))
    except Exception as e:
        app.updates_log(f"❌ FFmpeg 更新失敗: {e}\n")
        app.gui_update_queue.put(("update_failed", "ffmpeg"))

def worker_update_withnydl(app):
    app.updates_log("ℹ️ Withny 錄製核心已全面原生整合於 yt-dlp 中 (v2.0-native)，點擊上方「更新 yt-dlp」即可同步維護！\n")
    app.gui_update_queue.put(("update_done", ("withnydl", "原生整合 (v2.0-native)")))

def worker_update_rplay(app):
    app.updates_log("ℹ️ Rplay 下載核心已全面原生整合於 yt-dlp 中 (v2.4-native)，點擊上方「更新 yt-dlp」即可同步維護！\n")
    app.gui_update_queue.put(("update_done", ("rplay", "原生整合 (v2.4-native)")))
