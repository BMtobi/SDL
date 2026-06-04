import os
import sys
import re
import shutil
import zipfile
import subprocess
from config import BASE_DIR
from utils import get_binary_path

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
    fpath = os.path.join(BASE_DIR, "yt_dlp/extractor/rplaylive.py")
    if not os.path.exists(fpath):
        return "未安裝"
    return app.settings.get("rplay_version", "已安裝")

def get_local_version_withnydl(app):
    try:
        exe_path = get_binary_path("withny-dl-windows-amd64.exe")
        res = subprocess.run([exe_path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        out = res.stdout.strip()
        m = re.search(r'version (\S+)', out)
        if m:
            return m.group(1)
        return "已安裝"
    except Exception:
        return "未偵測到"

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
        
    # 3. Check Rplay Extractor (c-basalt/yt-dlp)
    latest_rplay = "未知"
    try:
        url = "https://api.github.com/repos/c-basalt/yt-dlp/commits?path=yt_dlp/extractor/rplaylive.py&sha=rplaylive"
        commits = fetch_url_json(url)
        if commits and isinstance(commits, list):
            latest_sha = commits[0].get("sha", "")[:7]
            latest_date = commits[0].get("commit", {}).get("author", {}).get("date", "")[:10]
            latest_rplay = f"{latest_sha} ({latest_date})"
        app.updates_log(f"   [Rplay Extractor] 最新線上版本: {latest_rplay}\n")
    except Exception as e:
        app.updates_log(f"❌ 檢查 [Rplay Extractor] 失敗: {e}\n")
        
    # 4. Check Withny-dl
    latest_withnydl = "未知"
    try:
        url = "https://api.github.com/repos/Darkness4/withny-dl/releases/latest"
        info = fetch_url_json(url)
        latest_withnydl = info.get("tag_name", "未知")
        app.updates_log(f"   [Withny-dl] 最新線上版本: {latest_withnydl}\n")
    except Exception as e:
        app.updates_log(f"❌ 檢查 [Withny-dl] 失敗: {e}\n")
        
    app.gui_update_queue.put(("check_updates_done", (latest_ytdlp, latest_ffmpeg, latest_rplay, latest_withnydl)))

def worker_update_ytdlp(app):
    import requests
    def fetch_url_json(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.json()

    def fetch_url_text(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.text.strip()

    try:
        url = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
        info = fetch_url_json(url)
        tag_name = info.get("tag_name")
        if not tag_name:
            raise Exception("無法取得最新版本標籤")
            
        rplay_path = os.path.join(BASE_DIR, "yt_dlp/extractor/rplaylive.py")
        rplay_content = None
        if os.path.exists(rplay_path):
            app.updates_log("📦 備份本地的 Rplay Extractor...\n")
            with open(rplay_path, "r", encoding="utf-8") as f:
                rplay_content = f.read()
                
        temp_dir = os.path.join(BASE_DIR, "temp_ytdlp_update")
        os.makedirs(temp_dir, exist_ok=True)
        zip_url = f"https://github.com/yt-dlp/yt-dlp/archive/refs/tags/{tag_name}.zip"
        zip_path = os.path.join(temp_dir, "ytdlp.zip")
        
        app.updates_log(f"📥 下載 yt-dlp 原始碼 ({tag_name})...\n")
        r = requests.get(zip_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        r.raise_for_status()
        with open(zip_path, 'wb') as f:
            f.write(r.content)
        
        app.updates_log("📂 解壓縮與覆蓋 yt-dlp 檔案...\n")
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
                            
        local_yt_dlp_dir = os.path.join(BASE_DIR, "yt_dlp")
        if os.path.exists(local_yt_dlp_dir):
            shutil.rmtree(local_yt_dlp_dir)
        shutil.copytree(os.path.join(temp_dir, "extracted_yt_dlp"), local_yt_dlp_dir)
        
        if rplay_content:
            app.updates_log("📦 還原 Rplay Extractor...\n")
            os.makedirs(os.path.dirname(rplay_path), exist_ok=True)
            with open(rplay_path, "w", encoding="utf-8") as f:
                f.write(rplay_content)
        else:
            app.updates_log("🌐 下載並整合 Rplay Extractor...\n")
            rplay_url = "https://raw.githubusercontent.com/c-basalt/yt-dlp/rplaylive/yt_dlp/extractor/rplaylive.py"
            rplay_file_content = fetch_url_text(rplay_url)
            os.makedirs(os.path.dirname(rplay_path), exist_ok=True)
            with open(rplay_path, "w", encoding="utf-8") as f:
                f.write(rplay_file_content)
                
        extractors_file = os.path.join(BASE_DIR, "yt_dlp/extractor/_extractors.py")
        if os.path.exists(extractors_file):
            with open(extractors_file, "r+", encoding="utf-8") as f:
                content = f.read()
                if "from .rplaylive import" not in content:
                    app.updates_log("🔗 登錄 Rplay Extractor 至核心...\n")
                    f.write("\nfrom .rplaylive import (RPlayLiveIE, RPlayUserIE, RPlayVideoIE)\n")
                    
        shutil.rmtree(temp_dir, ignore_errors=True)
        app.updates_log("✅ yt-dlp 更新成功！\n")
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

def worker_update_rplay(app):
    import requests
    def fetch_url_json(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.json()

    def fetch_url_text(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.text.strip()

    try:
        url = "https://api.github.com/repos/c-basalt/yt-dlp/commits?path=yt_dlp/extractor/rplaylive.py&sha=rplaylive"
        commits = fetch_url_json(url)
        if not commits or not isinstance(commits, list):
            raise Exception("無法取得最新 Commit 資訊")
        latest_sha = commits[0].get("sha", "")[:7]
        latest_date = commits[0].get("commit", {}).get("author", {}).get("date", "")[:10]
        version_str = f"{latest_sha} ({latest_date})"
        
        rplay_url = "https://raw.githubusercontent.com/c-basalt/yt-dlp/rplaylive/yt_dlp/extractor/rplaylive.py"
        app.updates_log(f"📥 下載最新 Rplay Extractor ({latest_sha})...\n")
        rplay_file_content = fetch_url_text(rplay_url)
        
        rplay_path = os.path.join(BASE_DIR, "yt_dlp/extractor/rplaylive.py")
        os.makedirs(os.path.dirname(rplay_path), exist_ok=True)
        with open(rplay_path, "w", encoding="utf-8") as f:
            f.write(rplay_file_content)
            
        extractors_file = os.path.join(BASE_DIR, "yt_dlp/extractor/_extractors.py")
        if os.path.exists(extractors_file):
            with open(extractors_file, "r+", encoding="utf-8") as f:
                content = f.read()
                if "from .rplaylive import" not in content:
                    app.updates_log("🔗 登錄 Rplay Extractor 至核心...\n")
                    f.write("\nfrom .rplaylive import (RPlayLiveIE, RPlayUserIE, RPlayVideoIE)\n")
                    
        app.settings["rplay_version"] = version_str
        app.save_settings()
        
        app.updates_log("✅ Rplay Extractor 更新成功！\n")
        app.gui_update_queue.put(("update_done", ("rplay", version_str)))
    except Exception as e:
        app.updates_log(f"❌ Rplay Extractor 更新失敗: {e}\n")
        app.gui_update_queue.put(("update_failed", "rplay"))

def worker_update_withnydl(app):
    import requests
    def fetch_url_json(url):
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        return r.json()

    try:
        url = "https://api.github.com/repos/Darkness4/withny-dl/releases/latest"
        info = fetch_url_json(url)
        tag_name = info.get("tag_name")
        if not tag_name:
            raise Exception("無法取得最新版本標籤")
            
        download_url = None
        assets = info.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            if "windows-amd64" in name and name.endswith(".exe"):
                download_url = asset.get("browser_download_url")
                break
                
        if not download_url:
            for asset in assets:
                name = asset.get("name", "")
                if "windows" in name or "amd64" in name:
                    download_url = asset.get("browser_download_url")
                    break
                    
        if not download_url:
            raise Exception("找不到適用於 Windows AMD64 的執行檔資源")
            
        target_exe = os.path.join(BASE_DIR, "withny-dl-windows-amd64.exe")
        
        if os.path.exists(target_exe):
            try:
                bak_path = target_exe + ".bak"
                if os.path.exists(bak_path):
                    os.remove(bak_path)
                os.rename(target_exe, bak_path)
            except Exception as e:
                app.updates_log(f"⚠️ 無法重命名舊的 withny-dl-windows-amd64.exe: {e}，將嘗試直接覆寫\n")
                
        app.updates_log(f"📥 下載 Withny-dl 執行檔 ({tag_name})...\n")
        r = requests.get(download_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=60)
        r.raise_for_status()
        with open(target_exe, 'wb') as f:
            f.write(r.content)
        
        bak_path = target_exe + ".bak"
        if os.path.exists(bak_path):
            try:
                os.remove(bak_path)
            except:
                pass
                
        app.updates_log("✅ Withny-dl 更新成功！\n")
        app.gui_update_queue.put(("update_done", ("withnydl", tag_name)))
    except Exception as e:
        app.updates_log(f"❌ Withny-dl 更新失敗: {e}\n")
        app.gui_update_queue.put(("update_failed", "withnydl"))
