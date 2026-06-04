import os
import sys
import re
import time
import subprocess
import shutil
from config import BASE_DIR, CREDENTIALS_FILE, CACHE_FILE
from utils import LazyModule, sanitize_filename, strip_ansi, get_binary_path

# Lazy load heavy dependencies
requests = LazyModule("requests")
yt_dlp = LazyModule("yt_dlp")

def get_ytdl_format_selector(quality):
    if quality == "1080p":
        return "bv*[height<=1080]+ba/b[height<=1080]"
    elif quality == "720p":
        return "bv*[height<=720]+ba/b[height<=720]"
    elif quality == "480p":
        return "bv*[height<=480]+ba/b[height<=480]"
    elif quality == "360p":
        return "bv*[height<=360]+ba/b[height<=360]"
    elif quality == "worst":
        return "wv*+wa/w"
    else:
        return "bv*+ba/b"

def extract_withny_id(url):
    clean_url = url.split('?')[0]
    if "channels/" in clean_url:
        match = re.search(r'channels/([^/?]+)', clean_url)
        if match:
            return match.group(1)
    return clean_url.rstrip('/').split('/')[-1]

def get_yt_live_metadata(url):
    ydl_opts = {
        'extract_flat': True,
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except:
        return None

def worker_manual_download(app, uid, url, custom_path, platform, quality="best", fmt="best"):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    
    display_name = url
    if len(display_name) > 40:
        display_name = display_name[:37] + "..."
        
    app.active_tasks[uid] = {
        "channel_name": f"[手動] {display_name}",
        "platform": platform,
        "status": "佇列中",
        "progress": 0.0,
        "speed": "",
        "size": "",
        "elapsed": "00:00",
        "start_time": time.time(),
        "process": None
    }
    app.gui_update_queue.put(("refresh_tasks", None))
    
    # Wait for queue limit
    while app.is_monitoring:
        running_count = 0
        for task in app.active_tasks.values():
            if task.get("process") is not None and task.get("status") != "監控中":
                running_count += 1
        if running_count < app.settings["max_concurrent_downloads"]:
            break
        time.sleep(1)
        
    if not app.is_monitoring:
        app.active_tasks.pop(uid, None)
        app.gui_update_queue.put(("refresh_tasks", None))
        return
        
    app.active_tasks[uid]["status"] = "解析連線中..."
    app.gui_update_queue.put(("refresh_tasks", None))
    
    start_time = time.time()
    try:
        # Determine output folder
        safe_name = sanitize_filename(custom_path) if custom_path else "ManualDownload"
        output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
        os.makedirs(output_dir, exist_ok=True)
        
        if platform == "Rplay":
            output_template = f"{output_dir}/[{safe_name}][%(upload_date>%Y-%m-%d)s][%(title)s].%(ext)s"
            cmd = [sys.executable, "-m", "yt_dlp", 
                   "--downloader-args", "ffmpeg:-loglevel warning",
                   url, "-o", output_template, "--no-progress", "--console-title"]
            if app.settings.get("rplay_username") and app.settings.get("rplay_password"):
                cmd.extend(["--username", app.settings["rplay_username"], "--password", app.settings["rplay_password"]])
            else:
                cmd.extend(["--extractor-args", f"rplaylive:jwt_token={app.settings['rplay_token']}"])
            
            if quality != "best":
                cmd.extend(["-f", get_ytdl_format_selector(quality)])
            if fmt != "best":
                cmd.extend(["--merge-output-format", fmt])
            
            clean_path, _ = app.prepare_clean_cookies()
            if clean_path:
                cmd.extend(["--cookies", clean_path])
                
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            app.active_tasks[uid]["process"] = proc
            
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    line = line.strip()
                    if "[download]" in line and "%" in line:
                        match = re.search(r'(\d+\.\d+)%', line)
                        if match:
                            app.active_tasks[uid]["progress"] = float(match.group(1)) / 100.0
                            app.active_tasks[uid]["status"] = "下載中"
                        speed_m = re.search(r'at\s+([^\s]+)', line)
                        if speed_m:
                            app.active_tasks[uid]["speed"] = speed_m.group(1)
                        size_m = re.search(r'of\s+([^\s]+)', line)
                        if size_m:
                            app.active_tasks[uid]["size"] = size_m.group(1)
                            
                        elapsed_sec = int(time.time() - start_time)
                        app.active_tasks[uid]["elapsed"] = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
                        app.gui_update_queue.put(("refresh_tasks", None))
                        
        elif platform == "YouTube":
            is_live_stream = False
            try:
                info = get_yt_live_metadata(url)
                if info:
                    video_info = info['entries'][0] if 'entries' in info and info['entries'] else info
                    if video_info:
                        is_live_stream = (video_info.get('live_status') == 'is_live' or video_info.get('is_live') is True)
            except:
                pass
                
            if is_live_stream:
                app.active_tasks[uid]["status"] = "錄影中"
                app.gui_update_queue.put(("refresh_tasks", None))
                
                output_template = f"{output_dir}/[{safe_name}][%(upload_date)s][%(title)s]"
                
                ytarchive_path = get_binary_path("ytarchive.exe")
                if ytarchive_path == "ytarchive.exe" and not shutil.which("ytarchive.exe") and shutil.which("ytarchive"):
                    ytarchive_path = "ytarchive"
                    
                cmd = [ytarchive_path, "--wait", "-o", output_template]
                
                clean_path, _ = app.prepare_clean_cookies()
                if clean_path and os.path.exists(clean_path):
                    cmd.extend(["--cookies", clean_path])
                    
                cmd.extend([url, quality])
                
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                app.active_tasks[uid]["process"] = proc
                
                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        line = line.strip()
                        size_match = re.search(r'([0-9\.]+\s*(?:MB|GB|KB))', line)
                        if size_match:
                            app.active_tasks[uid]["size"] = size_match.group(1)
                        elapsed_sec = int(time.time() - start_time)
                        app.active_tasks[uid]["elapsed"] = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
                        app.gui_update_queue.put(("refresh_tasks", None))
            else:
                output_template = f"{output_dir}/[{safe_name}][%(upload_date)s][%(title)s].%(ext)s"
                cmd = [sys.executable, "-m", "yt_dlp", url, "-o", output_template, "--no-progress", "--console-title"]
                
                if quality != "best":
                    cmd.extend(["-f", get_ytdl_format_selector(quality)])
                if fmt != "best":
                    cmd.extend(["--merge-output-format", fmt])
                
                clean_path, _ = app.prepare_clean_cookies()
                if clean_path:
                    cmd.extend(["--cookies", clean_path])
                    
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                app.active_tasks[uid]["process"] = proc
                
                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        line = line.strip()
                        if "[download]" in line and "%" in line:
                            match = re.search(r'(\d+\.\d+)%', line)
                            if match:
                                app.active_tasks[uid]["progress"] = float(match.group(1)) / 100.0
                                app.active_tasks[uid]["status"] = "下載中"
                            app.gui_update_queue.put(("refresh_tasks", None))
                        
        else:
            output_template = f"{output_dir}/[{safe_name}][%(title)s].%(ext)s"
            cmd = [sys.executable, "-m", "yt_dlp", url, "-o", output_template]
            
            if quality != "best":
                cmd.extend(["-f", get_ytdl_format_selector(quality)])
            if fmt != "best":
                cmd.extend(["--merge-output-format", fmt])
            
            is_fc2 = (platform == "FC2" or "fc2.com" in url.lower())
            if is_fc2:
                clean_path, _ = app.prepare_clean_fc2_cookies()
            else:
                clean_path, _ = app.prepare_clean_cookies()
                
            if clean_path:
                cmd.extend(["--cookies", clean_path])
                
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            app.active_tasks[uid]["process"] = proc
            
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                    
        returncode = proc.wait() if 'proc' in locals() else -1
        if returncode == 0:
            app.add_log(f"手動單獨下載完成: [手動] {display_name}", "SUCCESS")
            dl_file = app.find_newest_downloaded_file_in_dir(output_dir, start_time)
            if dl_file:
                app.add_history_entry(f"[手動] {display_name}", platform, os.path.basename(dl_file), dl_file)
        else:
            app.add_log(f"手動單獨下載失敗 (錯誤代碼: {returncode})", "ERROR")
            
    except Exception as e:
        app.add_log(f"手動單獨下載出錯: {e}", "ERROR")
        
    app.active_tasks.pop(uid, None)
    app.gui_update_queue.put(("refresh_tasks", None))

def worker_rplay(app, target):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    
    uid, name, url, do_record, img_url = target['uid'], target['name'], target['url'], target['record'], target['image']
    safe_name = sanitize_filename(name)
    output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
    
    is_live_locked = False
    consecutive_fails = 0
    
    while app.is_monitoring:
        if not do_record:
            info = app.check_rplay_status(url)
            if info:
                title = info.get('title', '無標題')
                if not is_live_locked:
                    app.add_log(f"{safe_name} Rplay 開台了！", "SUCCESS")
                    app.send_discord_notify(url, f"Rplay 開台: {title}", name, "Rplay", image_url=img_url)
                    is_live_locked = True
            else:
                is_live_locked = False
            time.sleep(60)
            continue
            
        if consecutive_fails >= 3:
            app.add_log(f"🚨 {safe_name} Rplay 連續異常中斷 3 次！暫停監控 10 分鐘。", "ERROR")
            for _ in range(600):
                if not app.is_monitoring:
                    break
                time.sleep(1)
            consecutive_fails = 0
            continue
            
        info = app.check_rplay_status(url)
        if info:
            title = info.get('title', '直播檔')
            app.add_log(f"{safe_name} Rplay 直播開始錄製...", "SUCCESS")
            app.send_discord_notify(url, f"Rplay 直播開始錄影: {title}", name, "Rplay", image_url=img_url)
            
            app.active_tasks[uid] = {
                "channel_name": safe_name,
                "platform": "Rplay",
                "status": "準備錄影",
                "progress": 0.0,
                "speed": "",
                "size": "",
                "elapsed": "00:00",
                "start_time": time.time(),
                "process": None
            }
            app.gui_update_queue.put(("refresh_tasks", None))
            app.gui_update_queue.put(("refresh_channels_list", None))
            
            os.makedirs(output_dir, exist_ok=True)
            output_template = f"{output_dir}/[{safe_name}][%(upload_date>%Y-%m-%d)s][%(title)s].%(ext)s"
            
            cmd = [sys.executable, "-m", "yt_dlp", 
                   "--downloader-args", "ffmpeg:-loglevel warning",
                   url, "-o", output_template, "--no-progress", "--console-title"]
            if app.settings.get("rplay_username") and app.settings.get("rplay_password"):
                cmd.extend(["--username", app.settings["rplay_username"], "--password", app.settings["rplay_password"]])
            else:
                cmd.extend(["--extractor-args", f"rplaylive:jwt_token={app.settings['rplay_token']}"])
            
            rplay_q = app.settings.get("rplay_quality", "best")
            rplay_f = app.settings.get("rplay_format", "best")
            if rplay_q != "best":
                cmd.extend(["-f", get_ytdl_format_selector(rplay_q)])
            if rplay_f != "best":
                cmd.extend(["--merge-output-format", rplay_f])
            
            clean_path, _ = app.prepare_clean_cookies()
            if clean_path and os.path.exists(clean_path):
                cmd.extend(["--cookies", clean_path])
                
            start_time = time.time()
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                app.active_tasks[uid]["process"] = proc
                
                while app.is_monitoring:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        line = line.strip()
                        if "[download]" in line and "%" in line:
                            match = re.search(r'(\d+\.\d+)%', line)
                            if match:
                                app.active_tasks[uid]["progress"] = float(match.group(1)) / 100.0
                                app.active_tasks[uid]["status"] = "錄影中"
                            speed_m = re.search(r'at\s+([^\s]+)', line)
                            if speed_m:
                                app.active_tasks[uid]["speed"] = speed_m.group(1)
                            size_m = re.search(r'of\s+([^\s]+)', line)
                            if size_m:
                                app.active_tasks[uid]["size"] = size_m.group(1)
                                
                        elapsed_sec = int(time.time() - start_time)
                        app.active_tasks[uid]["elapsed"] = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
                        
                duration = time.time() - start_time
                ret_code = proc.wait()
                
                if duration < 15:
                    consecutive_fails += 1
                    app.add_log(f"⚠️ {safe_name} 錄影異常秒斷 (僅 {int(duration)} 秒) [{consecutive_fails}/3]", "WARNING")
                else:
                    consecutive_fails = 0
                    
                app.add_log(f"{safe_name} Rplay 錄影結束", "WARNING")
                app.send_discord_notify(url, "Rplay 錄影結束", name, "Rplay", include_url=False)
                
                dl_file = app.find_newest_downloaded_file(name, start_time)
                if dl_file:
                    app.add_history_entry(name, "Rplay", os.path.basename(dl_file), dl_file)
                    
            except Exception as ex:
                app.add_log(f"Rplay 錄影異常: {ex}", "ERROR")
                
            app.active_tasks.pop(uid, None)
            app.gui_update_queue.put(("refresh_tasks", None))
            app.gui_update_queue.put(("refresh_channels_list", None))
            
            time.sleep(60)
        else:
            time.sleep(60)

def worker_youtube(app, target):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    
    uid, name, url, do_record, img_url = target['uid'], target['name'], target['url'], target['record'], target['image']
    safe_name = sanitize_filename(name)
    output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
    
    ignored_ids = set()
    is_live_locked = False
    last_vid = None
    
    while app.is_monitoring:
        info = get_yt_live_metadata(url)
        if info:
            video_info = info['entries'][0] if 'entries' in info and info['entries'] else info
            if video_info:
                v_title = video_info.get('title', '')
                v_id = video_info.get('id', '')
                v_status = video_info.get('live_status', 'unknown')
                
                if v_id in ignored_ids:
                    time.sleep(60)
                    continue
                    
                if v_status == 'is_live':
                    is_match = False
                    keywords = app.settings["yt_keywords"]
                    if not keywords:
                        is_match = True
                    else:
                        for kw in keywords:
                            if kw in v_title:
                                is_match = True
                                break
                                
                    if is_match:
                        if do_record:
                            app.add_log(f"{safe_name} YT 開台 & 開始錄影: {v_title}", "SUCCESS")
                            app.send_discord_notify(f"https://youtu.be/{v_id}", f"YT 錄影: {v_title}", name, "YouTube", image_url=img_url)
                            
                            app.active_tasks[uid] = {
                                "channel_name": safe_name,
                                "platform": "YouTube",
                                "status": "錄影中",
                                "progress": -1, # indeterminate
                                "speed": "",
                                "size": "",
                                "elapsed": "00:00",
                                "start_time": time.time(),
                                "process": None
                            }
                            app.gui_update_queue.put(("refresh_tasks", None))
                            app.gui_update_queue.put(("refresh_channels_list", None))
                            
                            os.makedirs(output_dir, exist_ok=True)
                            output_template = f"{output_dir}/[{safe_name}][%(upload_date)s][%(title)s]"
                            ytarchive_path = get_binary_path("ytarchive.exe")
                            if ytarchive_path == "ytarchive.exe" and not shutil.which("ytarchive.exe") and shutil.which("ytarchive"):
                                ytarchive_path = "ytarchive"
                            cmd = [ytarchive_path, "--wait", "-o", output_template]
                            
                            clean_path, _ = app.prepare_clean_cookies()
                            if clean_path and os.path.exists(clean_path):
                                cmd.extend(["--cookies", clean_path])
                                
                            cmd.extend([f"https://www.youtube.com/watch?v={v_id}", app.settings["yt_quality"]])
                            
                            start_time = time.time()
                            try:
                                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                                app.active_tasks[uid]["process"] = proc
                                
                                while app.is_monitoring:
                                    line = proc.stdout.readline()
                                    if not line and proc.poll() is not None:
                                        break
                                    if line:
                                        line = line.strip()
                                        size_match = re.search(r'([0-9\.]+\s*(?:MB|GB|KB))', line)
                                        if size_match:
                                            app.active_tasks[uid]["size"] = size_match.group(1)
                                        elapsed_sec = int(time.time() - start_time)
                                        app.active_tasks[uid]["elapsed"] = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
                                        
                                proc.wait()
                                
                                app.add_log(f"{safe_name} YT 錄影結束", "WARNING")
                                app.send_discord_notify(f"https://youtu.be/{v_id}", "YT 錄影完成", name, "YouTube", include_url=False)
                                
                                dl_file = app.find_newest_downloaded_file(name, start_time)
                                if dl_file:
                                    app.add_history_entry(name, "YouTube", os.path.basename(dl_file), dl_file)
                                    
                            except Exception as e:
                                app.add_log(f"YouTube 錄影異常: {e}", "ERROR")
                                
                            app.active_tasks.pop(uid, None)
                            app.gui_update_queue.put(("refresh_tasks", None))
                            app.gui_update_queue.put(("refresh_channels_list", None))
                            time.sleep(30)
                        else:
                            if not is_live_locked or last_vid != v_id:
                                app.add_log(f"{safe_name} YT 直播開台了！ {v_title}", "SUCCESS")
                                app.send_discord_notify(f"https://youtu.be/{v_id}", f"YT 開台: {v_title}", name, "YouTube", image_url=img_url)
                                is_live_locked = True
                                last_vid = v_id
                    else:
                        ignored_ids.add(v_id)
                else:
                    is_live_locked = False
        time.sleep(60)

def worker_withny_master(app, targets_map):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    
    exe_name = "withny-dl-windows-amd64.exe"
    exe_path = get_binary_path(exe_name)
    if exe_path == exe_name and not shutil.which(exe_name):
        app.add_log(f"錯誤: 找不到 Withny 下載器程式 ({exe_name})", "ERROR")
        return
        
    app.sync_withny_credentials()
    creds_abs_path = os.path.join(BASE_DIR, CREDENTIALS_FILE).replace("\\", "/")
    
    channels_yaml = ""
    for cid, info in targets_map.items():
        safe_chan_name = sanitize_filename(info['name'])
        channels_yaml += f"  '{cid}':\n    labels:\n      SafeName: \"{safe_chan_name}\"\n"
        
    config_content = f"""
credentialsFile: '{creds_abs_path}'
cachedCredentialsFile: '{CACHE_FILE}'
clearCredentialCacheOnFailureAfter: 10
loginRetryDelay: 60s

defaultParams:
  outFormat: '{app.settings["download_dir"]}/{{{{ .Labels.SafeName }}}}/[{{{{ .Date }}}}][Withny] {{{{ .Title }}}}.{{{{ .Ext }}}}'
  remux: {str(app.settings["withny_remux"]).lower()}
  remuxFormat: '{app.settings["withny_remux_format"]}'
  concat: {str(app.settings["withny_concat"]).lower()}
  keepIntermediates: {str(app.settings["withny_keep_intermediates"]).lower()}
  waitPollInterval: '{app.settings["withny_wait_poll_interval"]}'

rateLimitAvoidance:
  pollingPacing: {app.settings["withny_polling_pacing"]}

userAgent: '{app.settings["user_agent"]}'

channels:
{channels_yaml}
"""
    config_path = os.path.join(BASE_DIR, "master_config.yaml")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_content)
    except Exception as e:
        app.add_log(f"建立 master_config.yaml 失敗: {e}", "ERROR")
        return
        
    app.add_log(f"啟動 Withny 集中式監控 (監控 {len(targets_map)} 個頻道)...", "INFO")
    cmd = [exe_path, "watch", "-c", config_path, "--pprof.listen-address", "127.0.0.1:0"]
    
    auth_fail_count = 0
    local_tracker = {}
    
    while app.is_monitoring:
        try:
            for cid, info in targets_map.items():
                uid = info["uid"]
                if uid not in app.active_tasks:
                    app.active_tasks[uid] = {
                        "channel_name": info["name"],
                        "platform": "Withny",
                        "status": "監控中",
                        "progress": -1,
                        "speed": "",
                        "size": "",
                        "elapsed": "00:00",
                        "start_time": time.time(),
                        "process": None
                    }
            app.gui_update_queue.put(("refresh_tasks", None))
            app.gui_update_queue.put(("refresh_channels_list", None))
            
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=BASE_DIR, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            
            for info in targets_map.values():
                if info["uid"] in app.active_tasks:
                    app.active_tasks[info["uid"]]["process"] = proc
                    
            while app.is_monitoring:
                raw_line = proc.stdout.readline()
                if not raw_line and proc.poll() is not None:
                    break
                    
                if raw_line:
                    clean_line = strip_ansi(raw_line).strip()
                    line_lower = clean_line.lower()
                    
                    if ("401" in line_lower or "unauthorized" in line_lower or "400" in line_lower) and ("no credentials" in line_lower or "unexpected status code" in line_lower):
                        auth_fail_count += 1
                        app.add_log(f"Withny 授權錯誤 ({auth_fail_count}/3)，請確認 Session Token 是否有效", "WARNING")
                        if auth_fail_count >= 3:
                            proc.kill()
                            break
                            
                    if "watching channel" in line_lower:
                        auth_fail_count = 0
                        
                    try:
                        chan_match = re.search(r'filterChannelID=([^\s]+)', clean_line)
                        if not chan_match:
                            chan_match = re.search(r'channelID=([^\s]+)', clean_line)
                            
                        if chan_match:
                            channel_id = chan_match.group(1).strip().replace(',', '').replace('"', '')
                            if channel_id in targets_map:
                                target = targets_map[channel_id]
                                uid = target['uid']
                                
                                if "streams found" in line_lower:
                                    title_match = re.search(r'stream="([^"]+)"', clean_line)
                                    stream_title = title_match.group(1) if title_match else "直播開台"
                                    
                                    if local_tracker.get(channel_id, {}).get("status") != "LIVE":
                                        app.add_log(f"{target['name']} Withny 開台了！ {stream_title}", "SUCCESS")
                                        app.send_discord_notify(target['url'], f"Withny 開台: {stream_title}", target['name'], "Withny", image_url=target['image'])
                                        local_tracker[channel_id] = {"status": "LIVE", "title": stream_title, "start_time": time.time()}
                                        
                                    app.active_tasks[uid]["status"] = "準備錄影"
                                    app.active_tasks[uid]["elapsed"] = "00:00"
                                    app.gui_update_queue.put(("refresh_tasks", None))
                                    
                                elif "downloading" in line_lower or "recording" in line_lower:
                                    if local_tracker.get(channel_id, {}).get("status") != "REC":
                                        app.add_log(f"{target['name']} Withny 開始錄影", "SUCCESS")
                                        app.send_discord_notify(target['url'], "Withny 直播錄影中", target['name'], "Withny", include_url=False)
                                        local_tracker[channel_id] = {"status": "REC", "title": local_tracker.get(channel_id, {}).get("title", "Withny直播"), "start_time": time.time()}
                                        
                                    app.active_tasks[uid]["status"] = "錄影中"
                                    st = local_tracker[channel_id].get("start_time", time.time())
                                    elapsed_sec = int(time.time() - st)
                                    app.active_tasks[uid]["elapsed"] = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
                                    app.gui_update_queue.put(("refresh_tasks", None))
                                    
                                elif any(k in line_lower for k in ["offline", "waiting", "next check", "finished", "stopped", "done", "no streams"]):
                                    if local_tracker.get(channel_id, {}).get("status") in ["LIVE", "REC"]:
                                        was_rec = local_tracker.get(channel_id, {}).get("status") == "REC"
                                        app.add_log(f"{target['name']} Withny 錄影結束/已離線", "INFO")
                                        app.send_discord_notify(target['url'], "Withny 錄影結束", target['name'], "Withny", include_url=False)
                                        
                                        st = local_tracker[channel_id].get("start_time", time.time())
                                        local_tracker.pop(channel_id, None)
                                        
                                        if was_rec:
                                            dl_file = app.find_newest_downloaded_file(target["name"], st)
                                            if dl_file:
                                                app.add_history_entry(target["name"], "Withny", os.path.basename(dl_file), dl_file)
                                                
                                    app.active_tasks[uid]["status"] = "監控中"
                                    app.active_tasks[uid]["elapsed"] = "00:00"
                                    app.gui_update_queue.put(("refresh_tasks", None))
                    except Exception as inner_ex:
                        print(f"Error parsing Withny line: {inner_ex}")
                        
            proc.wait()
            app.add_log("Withny 監控核心程式已退出，將於 5 秒後重啟...", "WARNING")
            time.sleep(5)
            
        except Exception as e:
            app.add_log(f"Withny 監控異常: {e}", "ERROR")
            time.sleep(10)

def worker_withny_notify(app, target):
    uid = target["uid"]
    app.active_tasks[uid] = {
        "channel_name": target["name"],
        "platform": "Withny",
        "status": "監控中(僅通知)",
        "progress": -1,
        "speed": "",
        "size": "",
        "elapsed": "00:00",
        "start_time": time.time(),
        "process": None
    }
    app.gui_update_queue.put(("refresh_tasks", None))
    
    while app.is_monitoring:
        time.sleep(30)

def worker_fc2(app, target):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    
    uid, name, url, do_record, img_url = target['uid'], target['name'], target['url'], target['record'], target['image']
    safe_name = sanitize_filename(name)
    output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
    
    is_live_locked = False
    
    while app.is_monitoring:
        if not do_record:
            info = get_yt_live_metadata(url)
            if info and info.get('is_live') is True:
                title = info.get('title', 'FC2 直播')
                if not is_live_locked:
                    app.add_log(f"{safe_name} FC2 開台了！", "SUCCESS")
                    app.send_discord_notify(url, f"FC2 開台: {title}", name, "FC2", image_url=img_url)
                    is_live_locked = True
            else:
                is_live_locked = False
            time.sleep(60)
            continue
            
        app.add_log(f"{safe_name} FC2 直播監控中 (等待開台)...", "INFO")
        
        app.active_tasks[uid] = {
            "channel_name": safe_name,
            "platform": "FC2",
            "status": "監控中",
            "progress": -1,
            "speed": "",
            "size": "",
            "elapsed": "00:00",
            "start_time": time.time(),
            "process": None
        }
        app.gui_update_queue.put(("refresh_tasks", None))
        app.gui_update_queue.put(("refresh_channels_list", None))
        
        os.makedirs(output_dir, exist_ok=True)
        fc2_template = f"{output_dir}/[{safe_name}][{{date}}][{{title}}].{{ext}}"
        fc2_q = app.settings.get("fc2_quality", "3Mbps")
        fc2_f = app.settings.get("fc2_format", "mp4")
        
        cmd = ["fc2-live-dl", url, "--wait", "-o", fc2_template]
        if fc2_q:
            cmd.extend(["--quality", fc2_q])
        if fc2_f == "ts":
            cmd.append("--no-remux")
        
        clean_path, _ = app.prepare_clean_fc2_cookies()
        if clean_path and os.path.exists(clean_path):
            cmd.extend(["--cookies", clean_path])
            
        start_time = time.time()
        has_notified = False
        
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            app.active_tasks[uid]["process"] = proc
            
            while app.is_monitoring:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    line = line.strip()
                    if "Recording" in line:
                        app.active_tasks[uid]["status"] = "錄影中"
                        if not has_notified:
                            app.add_log(f"{safe_name} FC2 開始錄製！", "SUCCESS")
                            app.send_discord_notify(url, "FC2 直播開始錄影", name, "FC2", image_url=img_url)
                            has_notified = True
                            
                    elapsed_sec = int(time.time() - start_time)
                    app.active_tasks[uid]["elapsed"] = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
                    
            proc.wait()
            
            if has_notified:
                app.add_log(f"{safe_name} FC2 錄影結束", "WARNING")
                app.send_discord_notify(url, "FC2 錄影結束", name, "FC2", include_url=False)
                
                dl_file = app.find_newest_downloaded_file(name, start_time)
                if dl_file:
                    if fc2_f == "mkv" and dl_file.endswith(".mp4"):
                        mkv_file = dl_file[:-4] + ".mkv"
                        ffmpeg_cmd = [get_binary_path("ffmpeg.exe"), "-y", "-i", dl_file, "-c", "copy", mkv_file]
                        try:
                            subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                            if os.path.exists(mkv_file):
                                os.remove(dl_file)
                                dl_file = mkv_file
                        except Exception as ex:
                            app.add_log(f"FC2 轉檔至 MKV 失敗: {ex}", "WARNING")
                            
                    app.add_history_entry(name, "FC2", os.path.basename(dl_file), dl_file)
                    
        except Exception as e:
            app.add_log(f"FC2 監控異常: {e}", "ERROR")
            
        app.active_tasks.pop(uid, None)
        app.gui_update_queue.put(("refresh_tasks", None))
        app.gui_update_queue.put(("refresh_channels_list", None))
        time.sleep(10)
