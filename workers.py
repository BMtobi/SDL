import os
import json
import sys
import re
import time
import subprocess
import shutil
from config import BASE_DIR, CREDENTIALS_FILE, CACHE_FILE
from utils import LazyModule, sanitize_filename, strip_ansi, get_binary_path, kill_process_tree, clean_dir_leftovers

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

def get_rplay_butter_token():
    try:
        from Crypto.Cipher import AES
        salt = 'QWI@(!WAS)Dj1AA(!@*DJ#@$@~1)P'
        key = b'S%M@#H#B(!@()a2@'
        ts_value = str(int(time.time() / 360))
        iv = ts_value.zfill(16).encode('utf-8')
        raw_data = f'{salt}https://rplay.live{ts_value}'.encode('utf-8')
        pad_len = 16 - (len(raw_data) % 16)
        padded_data = raw_data + bytes([pad_len] * pad_len)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        enc = cipher.encrypt(padded_data)
        return enc.hex()
    except Exception:
        return ""

def resolve_rplay_url_to_stream_url(app, url):
    import re
    import base64
    import json
    import time
    
    headers = {
        'User-Agent': app.settings.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
        'Referer': 'https://rplay.live/',
        'Origin': 'https://rplay.live'
    }
    
    rplay_token = app.settings.get("rplay_token", "").strip()
    rplay_user_oid = app.settings.get("rplay_username", "").strip()
    
    # Check token validity and decode payload if present
    if rplay_token:
        try:
            parts = rplay_token.split('.')
            payload_data = None
            if len(parts) == 3:
                payload_b64 = parts[1] + '=' * (4 - len(parts[1]) % 4)
                payload_data = json.loads(base64.b64decode(payload_b64).decode('utf-8'))
            elif len(parts) == 1:
                payload_b64 = rplay_token + '=' * (4 - len(rplay_token) % 4)
                payload_data = json.loads(base64.b64decode(payload_b64).decode('utf-8'))
                
            if payload_data:
                exp_ts = payload_data.get('exp')
                if exp_ts and isinstance(exp_ts, (int, float)):
                    now_ts = int(time.time())
                    if now_ts > exp_ts:
                        exp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp_ts))
                        app.add_log(f"⚠️ Rplay Token 已於 {exp_str} 過期，請至設定中更新 Token！", "WARNING")
                
                if not rplay_user_oid:
                    rplay_user_oid = payload_data.get('_id') or payload_data.get('id') or payload_data.get('sub') or ""
        except Exception:
            pass
            
    if rplay_token and not rplay_user_oid:
        try:
            auth_headers = headers.copy()
            auth_headers["Authorization"] = rplay_token
            res = requests.get('https://api.rplay.live/account/getuser', headers=auth_headers, timeout=10)
            if res.status_code == 200:
                user_info = res.json()
                fetched_oid = user_info.get('_id') or user_info.get('id')
                if fetched_oid:
                    rplay_user_oid = fetched_oid
                    app.settings["rplay_username"] = fetched_oid
                    app.save_settings()
                    app.add_log(f"自動取得並更新 Rplay 使用者 OID: {fetched_oid}", "INFO")
        except Exception:
            pass

    auth_key = ""
    if rplay_token and rplay_user_oid:
        auth_headers = headers.copy()
        auth_headers["Authorization"] = rplay_token
        key_url = f"https://api.rplay.live/live/key2?lang=en&requestorOid={rplay_user_oid}"
        try:
            res = requests.get(key_url, headers=auth_headers, timeout=10)
            if res.status_code == 200:
                res_json = res.json()
                auth_key = res_json.get("authKey", "")
                if not auth_key:
                    region = res_json.get("region", "")
                    if region:
                        app.add_log(f"⚠️ Rplay 金鑰取得被拒絕 (回傳 region:{region})。請檢查 User OID ({rplay_user_oid}) 是否與 Token 匹配，或確認該頻道是否需要加入方案！", "WARNING")
                    else:
                        app.add_log(f"⚠️ Rplay 金鑰未回傳 authKey: {res.text}", "WARNING")
            else:
                app.add_log(f"⚠️ Rplay 金鑰請求失敗 (HTTP {res.status_code}): {res.text[:100]}", "WARNING")
        except Exception as e:
            app.add_log(f"⚠️ Rplay 金鑰請求異常: {e}", "WARNING")
    elif not rplay_token:
        app.add_log("ℹ️ 未設定 Rplay Token，將嘗試匿名取得串流（若該頻道為限定/防護直播將可能失敗）", "INFO")
    elif not rplay_user_oid:
        app.add_log("⚠️ 已設定 Rplay Token 但缺少 User OID，無法向伺服器請求授權金鑰！", "WARNING")

    # A. Check if URL is VOD / Play Content (e.g. /play/<id> or /content/<id>)
    m_play = re.search(r'https?://rplay\.live/(?:play|content|video)/(?P<id>[\d\w]+)', url)
    if m_play:
        content_id = m_play.group('id')
        try:
            c_headers = headers.copy()
            if rplay_token:
                c_headers["Authorization"] = rplay_token
            butter = get_rplay_butter_token()
            if butter:
                c_headers["Butter"] = butter
                
            query_params = {
                'contentOid': content_id,
                'status': 'published',
                'withComments': 'true',
                'requestCanView': 'true'
            }
            if rplay_user_oid:
                query_params['requestorOid'] = rplay_user_oid

            c_res = requests.get("https://api.rplay.live/content", params=query_params, headers=c_headers, timeout=10)
            if c_res.status_code == 200:
                cdata = c_res.json()
                can_view_obj = cdata.get('canView') or {}
                if isinstance(can_view_obj, dict):
                    media_url = can_view_obj.get('url') or can_view_obj.get('streamUrl')
                    if media_url:
                        return media_url
                    if can_view_obj.get('canView') is False:
                        try:
                            app.add_log(f"⚠️ Rplay 影片存檔 ({content_id}) 提示無播放權限 (可能為會員限定或需確認方案)", "WARNING")
                        except Exception:
                            pass
                
                rec_oid = cdata.get('recordOid')
                if rec_oid:
                    if auth_key:
                        return f"https://api.rplay.live/live/stream/playlist.m3u8?recordOid={rec_oid}&key2={auth_key}"
                    else:
                        return f"https://api.rplay.live/live/stream/playlist.m3u8?recordOid={rec_oid}"
                
                cr_oid = cdata.get('creatorOid')
                if cr_oid:
                    if auth_key:
                        return f"https://api.rplay.live/live/stream/playlist.m3u8?creatorOid={cr_oid}&key2={auth_key}"
                    else:
                        return f"https://api.rplay.live/live/stream/playlist.m3u8?creatorOid={cr_oid}"
        except Exception as e:
            try:
                app.add_log(f"Rplay 影片存檔解析失敗 ({content_id}): {e}", "WARNING")
            except Exception:
                pass
        return url

    # B. Live stream channel (/c/<name> or /live/<oid>)
    m = re.match(r'https?://rplay\.live/(?P<short>c|live)/(?P<id>[\d\w]+)', url)
    if not m:
        return url
    short = m.group('short')
    url_id = m.group('id')
    creator_oid = url_id

    if short == 'c':
        try:
            res = requests.get(f'https://api.rplay.live/account/getuser?customUrl={url_id}', headers=headers, timeout=10)
            if res.status_code == 200:
                creator_oid = res.json().get('_id', url_id)
        except Exception as e:
            app.add_log(f"Rplay 解析頻道 ID 失敗 ({url_id}): {e}", "WARNING")
            
    if auth_key:
        return f"https://api.rplay.live/live/stream/playlist.m3u8?creatorOid={creator_oid}&key2={auth_key}"
    else:
        return f"https://api.rplay.live/live/stream/playlist.m3u8?creatorOid={creator_oid}"


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
    env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    
    display_name = url
    if len(display_name) > 40:
        display_name = display_name[:37] + "..."
        
    app.active_tasks[uid] = {
        "channel_name": f"[手動] {display_name}",
        "platform": platform,
        "channel_url": url,
        "status": "佇列中",
        "progress": 0.0,
        "speed": "",
        "size": "",
        "elapsed": "00:00",
        "start_time": time.time(),
        "process": None
    }
    app.gui_update_queue.put(("refresh_tasks", None))
    
    # Wait for queue limit (concurrency limit, does not require global monitoring)
    while getattr(app, "is_running", True):
        running_count = 0
        for task in app.active_tasks.values():
            if task.get("process") is not None and task.get("status") not in ("監控中", "佇列中"):
                running_count += 1
        if running_count < app.settings.get("max_concurrent_downloads", 3):
            break
        time.sleep(1)
        
    app.active_tasks[uid]["status"] = "解析連線中..."
    app.gui_update_queue.put(("refresh_tasks", None))
    
    start_time = time.time()
    try:
        # Determine output folder
        if custom_path and (os.path.isabs(custom_path) or (len(custom_path) > 1 and custom_path[1] == ':') or custom_path.startswith('/')):
            output_dir = custom_path.replace("\\", "/")
            safe_name = sanitize_filename(os.path.basename(custom_path)) or "ManualDownload"
        else:
            safe_name = sanitize_filename(custom_path) if custom_path else "ManualDownload"
            output_dir = os.path.join(app.settings.get("download_dir", "."), safe_name).replace("\\", "/")
        os.makedirs(output_dir, exist_ok=True)
        
        is_batch = os.path.isfile(url) or url.lower().endswith(('.txt', '.list'))
        concurrent_frags = app.settings.get("concurrent_fragments", 8)
        try:
            concurrent_frags = int(concurrent_frags)
        except:
            concurrent_frags = 8
            
        last_error_lines = []
        
        def process_download_stream(process_obj):
            while True:
                line = process_obj.stdout.readline()
                if not line and process_obj.poll() is not None:
                    break
                if line:
                    line = line.strip()
                    if "ERROR:" in line or "[error]" in line.lower():
                        last_error_lines.append(line)
                        clean_err = re.sub(r'^ERROR:\s*(\[[^\]]+\])?\s*', '', line).strip()
                        if "RPLAY_AUTH_ERROR" in line or (platform == "Rplay" and "You do not have access" in clean_err):
                            app.add_log(f"🛑 [認證問題終止] 偵測到 Rplay 認證失效或 Token 過期: {clean_err}。已自動中斷任務以避免無效重試！", "ERROR")
                            app.paused_rplay_task = {
                                "url": url,
                                "custom_path": custom_path,
                                "platform": platform,
                                "quality": quality,
                                "fmt": fmt,
                                "display_name": display_name,
                                "is_batch": is_batch,
                                "time": time.time()
                            }
                            app.active_tasks[uid]["status"] = "Token失效已終止"
                            app.gui_update_queue.put(("refresh_tasks", None))
                            kill_process_tree(process_obj)
                            break
                        elif "404" in clean_err or "Not Found" in clean_err:
                            app.add_log(f"⚠️ [影片不存在/404] {clean_err}", "WARNING")
                        else:
                            app.add_log(f"⚠️ [下載錯誤] {clean_err}", "WARNING")
                    elif "Extracting URL:" in line:
                        m = re.search(r'Extracting URL:\s*([^\s]+)', line)
                        if m:
                            app.add_log(f"🔍 [解析項目] {m.group(1)}", "INFO")
                    elif "Destination:" in line:
                        m = re.search(r'Destination:\s*(.+)', line)
                        if m:
                            dest_name = os.path.basename(m.group(1).strip())
                            app.add_log(f"📥 [開始下載] {dest_name}", "INFO")
                    elif "100% of" in line and "at" in line:
                        app.add_log(f"✅ [項目下載完成] {line}", "SUCCESS")
                    elif "has already been downloaded" in line:
                        app.add_log(f"⏩ [已存在跳過] {line}", "INFO")
                        
                    if "Downloading item" in line or "Downloading video" in line or "[download] Downloading" in line:
                        item_m = re.search(r'Downloading (?:item|video)\s+(\d+\s+of\s+\d+)', line)
                        if item_m:
                            app.active_tasks[uid]["status"] = f"下載中 ({item_m.group(1)})"
                            app.add_log(f"📋 [批量進度] 目前正在下載項目 ({item_m.group(1)})", "INFO")
                    elif "[RPlayVideo] Extracting URL:" in line:
                        vid_m = re.search(r'/play/([a-f0-9]+)', line)
                        if vid_m:
                            app.active_tasks[uid]["status"] = f"解析中 ({vid_m.group(1)[:8]}...)"
                    elif "[download]" in line and "%" in line:
                        match = re.search(r'(\d+\.\d+)%', line)
                        if match:
                            app.active_tasks[uid]["progress"] = float(match.group(1)) / 100.0
                            if not app.active_tasks[uid]["status"].startswith("下載中 ("):
                                app.active_tasks[uid]["status"] = "下載中"
                        speed_m = re.search(r'at\s+([^\s]+)', line)
                        if speed_m:
                            app.active_tasks[uid]["speed"] = speed_m.group(1)
                        size_m = re.search(r'of\s+([^\s]+)', line)
                        if size_m:
                            app.active_tasks[uid]["size"] = size_m.group(1)
                            
                    app.gui_update_queue.put(("refresh_tasks", None))
            
        if platform == "Rplay":
            output_template = f"{output_dir}/[{safe_name}][%(upload_date>%Y-%m-%d,release_date>%Y-%m-%d,upload_date|NA)s][%(title)s].%(ext)s"
            
            butter_token = get_rplay_butter_token()
            rplay_user_oid = app.settings.get("rplay_username", "").strip()
            rplay_token = app.settings.get("rplay_token", "").strip()
            
            cmd = [sys.executable, "-m", "yt_dlp"]
            if is_batch:
                cmd.extend([
                    "-a", url,
                    "--ignore-errors",
                    "--no-abort-on-error",
                    "--retries", "10",
                    "--fragment-retries", "10",
                    "--skip-unavailable-fragments"
                ])
            else:
                cmd.append(url)
                
            cmd.extend([
                "-o", output_template, "--no-progress", "--console-title",
                "-N", str(concurrent_frags),
                "--buffer-size", "16M",
                "--http-chunk-size", "10M",
                "--add-header", "Referer:https://rplay.live/",
                "--add-header", "Origin:https://rplay.live"
            ])
                   
            if butter_token:
                cmd.extend(["--add-header", f"Butter:{butter_token}"])
            if rplay_user_oid:
                cmd.extend(["--add-header", f"rplay-private-content-requestor:{rplay_user_oid}"])
            if rplay_token:
                cmd.extend(["--add-header", f"Authorization:{rplay_token}"])
            
            if quality != "best":
                cmd.extend(["-f", get_ytdl_format_selector(quality)])
            if fmt != "best":
                cmd.extend(["--merge-output-format", fmt])
                
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            app.active_tasks[uid]["process"] = proc
            app.active_tasks[uid]["status"] = "排程下載中" if is_batch else "下載中"
            app.gui_update_queue.put(("refresh_tasks", None))
            
            process_download_stream(proc)
                        
        elif platform == "Withny":
            output_template = f"{output_dir}/[{safe_name}][%(upload_date>%Y-%m-%d,release_date>%Y-%m-%d,upload_date|NA)s][%(title)s].%(ext)s"
            withny_token = app.settings.get("withny_token", "").strip()
            
            cmd = [sys.executable, "-m", "yt_dlp"]
            if is_batch:
                cmd.extend([
                    "-a", url,
                    "--ignore-errors",
                    "--no-abort-on-error",
                    "--retries", "10",
                    "--fragment-retries", "10",
                    "--skip-unavailable-fragments"
                ])
            else:
                cmd.append(url)
                
            cmd.extend([
                "-o", output_template, "--no-progress", "--console-title",
                "-N", str(concurrent_frags),
                "--buffer-size", "16M",
                "--http-chunk-size", "10M",
                "--add-header", "Referer:https://www.withny.fun/",
                "--add-header", "Origin:https://www.withny.fun"
            ])
            
            if withny_token:
                cmd.extend(["--add-header", f"Cookie:__Secure-next-auth.session-token={withny_token}; next-auth.session-token={withny_token}"])
                
            if quality != "best":
                cmd.extend(["-f", get_ytdl_format_selector(quality)])
            if fmt != "best":
                cmd.extend(["--merge-output-format", fmt])
                
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            app.active_tasks[uid]["process"] = proc
            app.active_tasks[uid]["status"] = "排程下載中" if is_batch else "下載中"
            app.gui_update_queue.put(("refresh_tasks", None))
            
            process_download_stream(proc)
                        
        elif platform == "YouTube":
            is_live_stream = False
            if not is_batch:
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
                        app.gui_update_queue.put(("refresh_tasks", None))
            else:
                output_template = f"{output_dir}/[{safe_name}][%(upload_date>%Y-%m-%d,release_date>%Y-%m-%d,upload_date|NA)s][%(title)s].%(ext)s"
                cmd = [sys.executable, "-m", "yt_dlp"]
                if is_batch:
                    cmd.extend([
                        "-a", url,
                        "--ignore-errors",
                        "--no-abort-on-error",
                        "--retries", "10",
                        "--fragment-retries", "10",
                        "--skip-unavailable-fragments"
                    ])
                else:
                    cmd.append(url)
                cmd.extend([
                    "-o", output_template, "--no-progress", "--console-title",
                    "-N", str(concurrent_frags),
                    "--buffer-size", "16M",
                    "--http-chunk-size", "10M"
                ])
                
                if quality != "best":
                    cmd.extend(["-f", get_ytdl_format_selector(quality)])
                if fmt != "best":
                    cmd.extend(["--merge-output-format", fmt])
                
                clean_path, _ = app.prepare_clean_cookies()
                if clean_path:
                    cmd.extend(["--cookies", clean_path])
                    
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                app.active_tasks[uid]["process"] = proc
                
                process_download_stream(proc)
                        
        else:
            output_template = f"{output_dir}/[{safe_name}][%(upload_date>%Y-%m-%d,release_date>%Y-%m-%d,upload_date|NA)s][%(title)s].%(ext)s"
            cmd = [sys.executable, "-m", "yt_dlp"]
            if is_batch:
                cmd.extend([
                    "-a", url,
                    "--ignore-errors",
                    "--no-abort-on-error",
                    "--retries", "10",
                    "--fragment-retries", "10",
                    "--skip-unavailable-fragments"
                ])
            else:
                cmd.append(url)
            cmd.extend([
                "-o", output_template,
                "-N", str(concurrent_frags),
                "--buffer-size", "16M",
                "--http-chunk-size", "10M"
            ])
            
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
            
            process_download_stream(proc)
                    
        returncode = proc.wait() if 'proc' in locals() else -1
        if returncode == 0:
            if is_batch:
                app.add_log(f"🎉 手動批量排程全部項目下載完成: [批量] {display_name}", "SUCCESS")
            else:
                app.add_log(f"手動單獨下載完成: [手動] {display_name}", "SUCCESS")
                dl_file = app.find_newest_downloaded_file_in_dir(output_dir, start_time)
                if dl_file:
                    app.add_history_entry(f"[手動] {display_name}", platform, os.path.basename(dl_file), dl_file)
        else:
            if is_batch:
                app.add_log(f"手動批量排程已執行結束: [批量] {display_name} (排程已跑完全部網址，部分失敗項目已在上表記錄)", "INFO")
            else:
                if last_error_lines:
                    clean_err = re.sub(r'^ERROR:\s*(\[[^\]]+\])?\s*', '', last_error_lines[-1]).strip()
                    if "You do not have access" in clean_err:
                        app.add_log(f"下載失敗: {display_name} -> 此影片為付費/會員限定內容 (當前帳號無觀看權限)", "WARNING")
                    elif "404" in clean_err or "Not Found" in clean_err:
                        app.add_log(f"下載失敗: {display_name} -> 影片不存在或已被作者下架 (404)", "WARNING")
                    else:
                        app.add_log(f"手動單獨下載失敗: {clean_err}", "ERROR")
                else:
                    app.add_log(f"手動單獨下載失敗 (錯誤代碼: {returncode})", "ERROR")
            
        try:
            clean_dir_leftovers(output_dir)
        except:
            pass
            
    except Exception as e:
        app.add_log(f"手動下載出錯: {e}", "ERROR")
        
    app.active_tasks.pop(uid, None)
    app.gui_update_queue.put(("refresh_tasks", None))

def worker_rplay(app, target):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    
    uid, name, url, do_record, img_url = target['uid'], target['name'], target['url'], target['record'], target['image']
    safe_name = sanitize_filename(name)
    output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
    
    is_live_locked = False
    consecutive_fails = 0
    session_id = getattr(app, "monitor_session_id", 0)
    
    while app.is_monitoring and session_id == getattr(app, "monitor_session_id", 0):
        if not target.get("record"):
            info = app.check_rplay_status(url)
            if info:
                title = info.get('title', '無標題')
                if not is_live_locked:
                    app.add_log(f"{safe_name} Rplay 開台了！", "SUCCESS")
                    is_live_locked = True
                    
                    app.active_tasks[uid] = {
                        "channel_name": safe_name,
                        "platform": "Rplay",
                        "channel_url": url,
                        "status": "直播中(僅通知)",
                        "progress": -1,
                        "speed": "",
                        "size": "",
                        "elapsed": "00:00",
                        "start_time": time.time(),
                        "process": None,
                        "target_ref": target,
                        "image_url": img_url
                    }
                    app.discord_notify_start(uid, url, title, name, "Rplay", image_url=img_url, can_record=True)
                    app.gui_update_queue.put(("refresh_tasks", None))
                    app.gui_update_queue.put(("refresh_channels_list", None))
            else:
                if is_live_locked:
                    is_live_locked = False
                    app.discord_notify_end(uid, has_saved=False)
                    app.active_tasks.pop(uid, None)
                    app.gui_update_queue.put(("refresh_tasks", None))
                    app.gui_update_queue.put(("refresh_channels_list", None))
                    
            for _ in range(60):
                if not app.is_monitoring or session_id != getattr(app, "monitor_session_id", 0) or target.get("record"):
                    break
                time.sleep(1)
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
            
            was_no = False
            if uid in app.active_tasks:
                was_no = app.active_tasks[uid].get("was_notify_only", False)
                
            app.discord_notify_start(uid, url, title, name, "Rplay", image_url=img_url)
            
            app.active_tasks[uid] = {
                "channel_name": safe_name,
                "platform": "Rplay",
                "channel_url": url,
                "status": "準備錄影",
                "progress": 0.0,
                "speed": "",
                "size": "",
                "elapsed": "00:00",
                "start_time": time.time(),
                "process": None,
                "target_ref": target,
                "was_notify_only": was_no
            }
            app.gui_update_queue.put(("refresh_tasks", None))
            app.gui_update_queue.put(("refresh_channels_list", None))
            
            os.makedirs(output_dir, exist_ok=True)
            date_str = time.strftime("%Y-%m-%d")
            time_suffix = time.strftime("%Y-%m-%d %H_%M")
            title_sanitized = sanitize_filename(title)
            full_title = f"{title_sanitized} {time_suffix}"
            full_title = sanitize_filename(full_title)
            output_template = f"{output_dir}/[{safe_name}][{date_str}][{full_title}].%(ext)s"
            
            stream_url = resolve_rplay_url_to_stream_url(app, url)
            
            concurrent_frags = app.settings.get("concurrent_fragments", 8)
            try:
                concurrent_frags = int(concurrent_frags)
            except:
                concurrent_frags = 8
                
            cmd = [sys.executable, "-m", "yt_dlp", 
                   "--downloader-args", "ffmpeg:-loglevel warning",
                   stream_url, "-o", output_template, "--no-progress", "--console-title",
                   "-N", str(concurrent_frags),
                   "--buffer-size", "16M",
                   "--http-chunk-size", "10M",
                   "--add-header", "Referer:https://rplay.live",
                   "--add-header", "Origin:https://rplay.live"]
            
            rplay_q = app.settings.get("rplay_quality", "best")
            rplay_f = app.settings.get("rplay_format", "best")
            if rplay_q != "best":
                cmd.extend(["-f", get_ytdl_format_selector(rplay_q)])
            if rplay_f != "best":
                cmd.extend(["--merge-output-format", rplay_f])
                
            start_time = time.time()
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                app.active_tasks[uid]["process"] = proc
                
                output_lines = []
                while app.is_monitoring:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        line = line.strip()
                        output_lines.append(line)
                        if len(output_lines) > 20:
                            output_lines.pop(0)
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
                                
                        app.gui_update_queue.put(("refresh_tasks", None))
                        
                if not app.is_monitoring and proc.poll() is None:
                    kill_process_tree(proc)
                    
                duration = time.time() - start_time
                ret_code = proc.wait()
                
                try:
                    clean_dir_leftovers(output_dir)
                except Exception as cle:
                    app.add_log(f"清理 Rplay 暫存檔失敗: {cle}", "WARNING")
                
                if duration < 15:
                    consecutive_fails += 1
                    app.add_log(f"⚠️ {safe_name} 錄影異常秒斷 (僅 {int(duration)} 秒) [{consecutive_fails}/3]", "WARNING")
                    has_auth_err = False
                    for ol in output_lines[-5:]:
                        app.add_log(f"  [yt-dlp] {ol}", "ERROR")
                        if "401" in ol or "unauthorized" in ol.lower() or "forbidden" in ol.lower():
                            has_auth_err = True
                    if has_auth_err:
                        app.add_log(f"🚨 Rplay 授權登入失敗 (401 Unauthorized)，請檢查並更新 Rplay Token 與 User OID！", "ERROR")
                else:
                    consecutive_fails = 0
                    
                app.add_log(f"{safe_name} Rplay 錄影結束", "WARNING")
                dl_file = app.find_newest_downloaded_file(name, start_time)
                if dl_file:
                    app.add_history_entry(name, "Rplay", os.path.basename(dl_file), dl_file)
                    app.discord_notify_end(uid, has_saved=True)
                else:
                    app.discord_notify_end(uid, has_saved=False)
                    
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
    env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    
    uid, name, url, do_record, img_url = target['uid'], target['name'], target['url'], target['record'], target['image']
    safe_name = sanitize_filename(name)
    output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
    
    ignored_ids = set()
    is_live_locked = False
    last_vid = None
    session_id = getattr(app, "monitor_session_id", 0)
    
    while app.is_monitoring and session_id == getattr(app, "monitor_session_id", 0):
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
                        if target.get("record"):
                            app.add_log(f"{safe_name} YT 開台 & 開始錄影: {v_title}", "SUCCESS")
                            
                            was_no = False
                            if uid in app.active_tasks:
                                was_no = app.active_tasks[uid].get("was_notify_only", False)
                                
                            app.discord_notify_start(uid, f"https://youtu.be/{v_id}", v_title, name, "YouTube", image_url=img_url)
                            
                            app.active_tasks[uid] = {
                                "channel_name": safe_name,
                                "platform": "YouTube",
                                "channel_url": url,
                                "status": "錄影中",
                                "progress": -1,
                                "speed": "",
                                "size": "",
                                "elapsed": "00:00",
                                "start_time": time.time(),
                                "process": None,
                                "target_ref": target,
                                "was_notify_only": was_no
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
                                        app.gui_update_queue.put(("refresh_tasks", None))
                                        
                                if not app.is_monitoring and proc.poll() is None:
                                    kill_process_tree(proc)
                                proc.wait()
                                
                                try:
                                    clean_dir_leftovers(output_dir)
                                except:
                                    pass
                                
                                app.add_log(f"{safe_name} YT 錄影結束", "WARNING")
                                dl_file = app.find_newest_downloaded_file(name, start_time)
                                if dl_file:
                                    app.add_history_entry(name, "YouTube", os.path.basename(dl_file), dl_file)
                                    app.discord_notify_end(uid, has_saved=True)
                                else:
                                    app.discord_notify_end(uid, has_saved=False)
                                    
                            except Exception as e:
                                app.add_log(f"YouTube 錄影異常: {e}", "ERROR")
                                
                            app.active_tasks.pop(uid, None)
                            app.gui_update_queue.put(("refresh_tasks", None))
                            app.gui_update_queue.put(("refresh_channels_list", None))
                            time.sleep(30)
                        else:
                            if not is_live_locked or last_vid != v_id:
                                app.add_log(f"{safe_name} YT 直播開台了！ {v_title}", "SUCCESS")
                                is_live_locked = True
                                last_vid = v_id
                                
                                app.active_tasks[uid] = {
                                    "channel_name": safe_name,
                                    "platform": "YouTube",
                                    "channel_url": url,
                                    "status": "直播中(僅通知)",
                                    "progress": -1,
                                    "speed": "",
                                    "size": "",
                                    "elapsed": "00:00",
                                    "start_time": time.time(),
                                    "process": None,
                                    "target_ref": target,
                                    "image_url": img_url
                                }
                                app.discord_notify_start(uid, f"https://youtu.be/{v_id}", v_title, name, "YouTube", image_url=img_url, can_record=True)
                                app.gui_update_queue.put(("refresh_tasks", None))
                                app.gui_update_queue.put(("refresh_channels_list", None))
                    else:
                        ignored_ids.add(v_id)
                else:
                    if is_live_locked:
                        is_live_locked = False
                        app.discord_notify_end(uid, has_saved=False)
                        app.active_tasks.pop(uid, None)
                        app.gui_update_queue.put(("refresh_tasks", None))
                        app.gui_update_queue.put(("refresh_channels_list", None))
        else:
            if is_live_locked:
                is_live_locked = False
                app.discord_notify_end(uid, has_saved=False)
                app.active_tasks.pop(uid, None)
                app.gui_update_queue.put(("refresh_tasks", None))
                app.gui_update_queue.put(("refresh_channels_list", None))
                
        for _ in range(60):
            if not app.is_monitoring or session_id != getattr(app, "monitor_session_id", 0) or (target.get("record") and is_live_locked):
                break
            time.sleep(1)

def check_withny_live_status(url):
    import requests
    import re
    import json
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            for match in re.findall(r'<script\b[^>]*>self\.__next_f\.push\((\[.+?\])\)</script>', res.text, flags=re.DOTALL):
                if 'initialCast' in match:
                    try:
                        parsed = json.loads(match)
                        if len(parsed) >= 2 and isinstance(parsed[1], str):
                            s = parsed[1]
                            idx = s.find('[')
                            if idx != -1:
                                data = json.loads(s[idx:])
                                def find_ivs(obj):
                                    if isinstance(obj, dict):
                                        if 'ivsChannel' in obj and isinstance(obj['ivsChannel'], dict):
                                            return obj['ivsChannel']
                                        for k, v in obj.items():
                                            r = find_ivs(v)
                                            if r:
                                                return r
                                    elif isinstance(obj, list):
                                        for item in obj:
                                            r = find_ivs(item)
                                            if r:
                                                return r
                                    return None
                                ivs = find_ivs(data)
                                if ivs and isinstance(ivs, dict):
                                    return ivs.get('state') == 'live'
                    except Exception:
                        pass
    except Exception:
        pass
    return False

def worker_withny(app, target):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    
    uid, name, url, do_record, img_url = target['uid'], target['name'], target['url'], target['record'], target['image']
    safe_name = sanitize_filename(name)
    output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
    
    is_live_locked = False
    session_id = getattr(app, "monitor_session_id", 0)
    consecutive_fails = 0
    
    while app.is_monitoring and session_id == getattr(app, "monitor_session_id", 0):
        if not do_record:
            is_live = check_withny_live_status(url)
            if is_live:
                if not is_live_locked:
                    app.add_log(f"{safe_name} Withny 開台了！", "SUCCESS")
                    is_live_locked = True
                    app.active_tasks[uid] = {
                        "channel_name": safe_name,
                        "platform": "Withny",
                        "channel_url": url,
                        "status": "直播中(僅通知)",
                        "progress": -1,
                        "speed": "",
                        "size": "",
                        "elapsed": "00:00",
                        "start_time": time.time(),
                        "process": None,
                        "target_ref": target,
                        "image_url": img_url
                    }
                    app.discord_notify_start(uid, url, "Withny直播", name, "Withny", image_url=img_url, can_record=True)
                    app.gui_update_queue.put(("refresh_tasks", None))
                    app.gui_update_queue.put(("refresh_channels_list", None))
            else:
                if is_live_locked:
                    is_live_locked = False
                    app.discord_notify_end(uid, has_saved=False)
                    app.active_tasks.pop(uid, None)
                    app.gui_update_queue.put(("refresh_tasks", None))
                    app.gui_update_queue.put(("refresh_channels_list", None))
                    
            for _ in range(60):
                if not app.is_monitoring or session_id != getattr(app, "monitor_session_id", 0) or target.get("record"):
                    break
                time.sleep(1)
            continue
            
        if consecutive_fails >= 3:
            app.add_log(f"🚨 {safe_name} Withny 連續異常中斷 3 次！暫停監控 10 分鐘。", "ERROR")
            for _ in range(600):
                if not app.is_monitoring:
                    break
                time.sleep(1)
            consecutive_fails = 0
            continue
            
        is_live = check_withny_live_status(url)
        if is_live:
            app.add_log(f"{safe_name} Withny 直播開始錄製...", "SUCCESS")
            title = "Withny直播"
            
            was_no = False
            if uid in app.active_tasks:
                was_no = app.active_tasks[uid].get("was_notify_only", False)
                
            app.discord_notify_start(uid, url, title, name, "Withny", image_url=img_url)
            
            app.active_tasks[uid] = {
                "channel_name": safe_name,
                "platform": "Withny",
                "channel_url": url,
                "status": "準備錄影",
                "progress": 0.0,
                "speed": "",
                "size": "",
                "elapsed": "00:00",
                "start_time": time.time(),
                "process": None,
                "target_ref": target,
                "was_notify_only": was_no
            }
            app.gui_update_queue.put(("refresh_tasks", None))
            app.gui_update_queue.put(("refresh_channels_list", None))
            
            os.makedirs(output_dir, exist_ok=True)
            date_str = time.strftime("%Y-%m-%d")
            time_suffix = time.strftime("%Y-%m-%d %H_%M")
            output_template = f"{output_dir}/[{safe_name}][{date_str}][{time_suffix}].%(ext)s"
            
            concurrent_frags = app.settings.get("concurrent_fragments", 8)
            try:
                concurrent_frags = int(concurrent_frags)
            except:
                concurrent_frags = 8
                
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "--downloader-args", "ffmpeg:-loglevel warning",
                url,
                "-o", output_template,
                "--no-progress", "--console-title",
                "-N", str(concurrent_frags),
                "--buffer-size", "16M",
                "--http-chunk-size", "10M",
                "--add-header", "Referer:https://www.withny.fun/",
                "--add-header", "Origin:https://www.withny.fun"
            ]
            
            withny_token = app.settings.get("withny_token", "").strip()
            if withny_token:
                cmd.extend(["--add-header", f"Cookie:__Secure-next-auth.session-token={withny_token}; next-auth.session-token={withny_token}"])
                
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env, cwd=output_dir, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            app.active_tasks[uid]["process"] = proc
            app.active_tasks[uid]["status"] = "錄影中"
            app.gui_update_queue.put(("refresh_tasks", None))
            
            start_t = time.time()
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
                    speed_m = re.search(r'at\s+([^\s]+)', line)
                    if speed_m:
                        app.active_tasks[uid]["speed"] = speed_m.group(1)
                    size_m = re.search(r'of\s+([^\s]+)', line)
                    if size_m:
                        app.active_tasks[uid]["size"] = size_m.group(1)
                    app.gui_update_queue.put(("refresh_tasks", None))
                    
            return_code = proc.wait()
            app.add_log(f"{safe_name} Withny 直播錄製結束 (代碼: {return_code})", "INFO")
            
            if (time.time() - start_t) < 30 and return_code != 0:
                consecutive_fails += 1
            else:
                consecutive_fails = 0
                
            dl_file = app.find_newest_downloaded_file(safe_name, start_t)
            if dl_file:
                app.add_history_entry(safe_name, "Withny", os.path.basename(dl_file), dl_file)
                app.discord_notify_end(uid, has_saved=True)
            else:
                app.discord_notify_end(uid, has_saved=False)
                
            app.active_tasks.pop(uid, None)
            app.gui_update_queue.put(("refresh_tasks", None))
            app.gui_update_queue.put(("refresh_channels_list", None))
            
            try:
                clean_dir_leftovers(output_dir)
            except:
                pass
                
        for _ in range(40):
            if not app.is_monitoring or session_id != getattr(app, "monitor_session_id", 0):
                break
            time.sleep(1)

def worker_fc2(app, target):
    env = os.environ.copy()
    env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    
    uid, name, url, do_record, img_url = target['uid'], target['name'], target['url'], target['record'], target['image']
    safe_name = sanitize_filename(name)
    output_dir = os.path.join(app.settings["download_dir"], safe_name).replace("\\", "/")
    
    is_live_locked = False
    session_id = getattr(app, "monitor_session_id", 0)
    
    while app.is_monitoring and session_id == getattr(app, "monitor_session_id", 0):
        if not target.get("record"):
            info = get_yt_live_metadata(url)
            if info and info.get('is_live') is True:
                title = info.get('title', 'FC2 直播')
                if not is_live_locked:
                    app.add_log(f"{safe_name} FC2 開台了！", "SUCCESS")
                    is_live_locked = True
                    
                    app.active_tasks[uid] = {
                        "channel_name": safe_name,
                        "platform": "FC2",
                        "channel_url": url,
                        "status": "直播中(僅通知)",
                        "progress": -1,
                        "speed": "",
                        "size": "",
                        "elapsed": "00:00",
                        "start_time": time.time(),
                        "process": None,
                        "target_ref": target,
                        "image_url": img_url
                    }
                    app.discord_notify_start(uid, url, title, name, "FC2", image_url=img_url, can_record=True)
                    app.gui_update_queue.put(("refresh_tasks", None))
                    app.gui_update_queue.put(("refresh_channels_list", None))
            else:
                if is_live_locked:
                    is_live_locked = False
                    app.discord_notify_end(uid, has_saved=False)
                    app.active_tasks.pop(uid, None)
                    app.gui_update_queue.put(("refresh_tasks", None))
                    app.gui_update_queue.put(("refresh_channels_list", None))
                    
            for _ in range(60):
                if not app.is_monitoring or session_id != getattr(app, "monitor_session_id", 0) or target.get("record"):
                    break
                time.sleep(1)
            continue
            
        app.add_log(f"{safe_name} FC2 直播監控中 (等待開台)...", "INFO")
        
        was_no = False
        if uid in app.active_tasks:
            was_no = app.active_tasks[uid].get("was_notify_only", False)
            
        app.active_tasks[uid] = {
            "channel_name": safe_name,
            "platform": "FC2",
            "channel_url": url,
            "status": "準備錄影" if was_no else "監控中",
            "progress": -1,
            "speed": "",
            "size": "",
            "elapsed": "00:00",
            "start_time": time.time(),
            "process": None,
            "target_ref": target,
            "was_notify_only": was_no
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
                            start_time = time.time()
                            app.active_tasks[uid]["start_time"] = start_time
                            app.add_log(f"{safe_name} FC2 開始錄製！", "SUCCESS")
                            app.discord_notify_start(uid, url, "FC2 直播錄影", name, "FC2", image_url=img_url)
                            has_notified = True
                            
                    if has_notified:
                        app.gui_update_queue.put(("refresh_tasks", None))
                    
            if not app.is_monitoring and proc.poll() is None:
                kill_process_tree(proc)
            proc.wait()
            
            try:
                clean_dir_leftovers(output_dir)
            except:
                pass
            
            if has_notified:
                app.add_log(f"{safe_name} FC2 錄影結束", "WARNING")
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
                    app.discord_notify_end(uid, has_saved=True)
                else:
                    app.discord_notify_end(uid, has_saved=False)
                    
        except Exception as e:
            app.add_log(f"FC2 監控異常: {e}", "ERROR")
            
        app.active_tasks.pop(uid, None)
        app.gui_update_queue.put(("refresh_tasks", None))
        app.gui_update_queue.put(("refresh_channels_list", None))
        time.sleep(10)
