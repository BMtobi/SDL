import itertools
import json
import os
import random
import re
import time

from .common import InfoExtractor
from ..networking import Request
from ..utils import (
    ExtractorError,
    UserNotLive,
    clean_html,
    int_or_none,
    jwt_decode_hs256,
    parse_iso8601,
    traverse_obj,
    url_or_none,
)


class WithnyBaseIE(InfoExtractor):
    _withny_token = None

    def _setup_credentials(self):
        # Dynamically load Withny session token from settings.json if available
        try:
            for p in ['settings.json', '../settings.json', 'C:/AI pro/grind code/StreamBot/settings.json']:
                if os.path.exists(p):
                    with open(p, 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                        if tok := cfg.get('withny_token'):
                            self._withny_token = tok.strip()
                            break
        except Exception:
            pass

        # If not found in settings.json, check credentials.yaml
        if not self._withny_token:
            try:
                for p in ['credentials.yaml', '../credentials.yaml', 'C:/AI pro/grind code/StreamBot/credentials.yaml']:
                    if os.path.exists(p):
                        with open(p, 'r', encoding='utf-8') as f:
                            for line in f:
                                if 'session_token:' in line or 'token:' in line:
                                    tok = line.split(':', 1)[1].strip().strip('"\'')
                                    if tok and len(tok) > 20:
                                        self._withny_token = tok
                                        break
            except Exception:
                pass

        if self._withny_token:
            self._set_cookie('.withny.fun', '__Secure-next-auth.session-token', self._withny_token, discard=True)
            self._set_cookie('.withny.fun', 'next-auth.session-token', self._withny_token, discard=True)

    def _download_webpage(self, url, video_id, login_msg='You need to login to access video (請確認 Withny Session Token 是否已設定且有效)', **kwargs):
        self._setup_credentials()
        webpage, urlh = self._download_webpage_handle(url, video_id, **kwargs)
        if urlh.url.startswith('https://www.withny.fun/login'):
            self.raise_login_required(login_msg)
        return webpage

    def _search_next_seg(self, keyword, webpage, video_id):
        # Parse Next.js App Router streaming RSC data: self.__next_f.push([...])
        results = []
        for raw_match in re.findall(r'<script\b[^>]*>self\.__next_f\.push\((\[.+?\])\)</script>', webpage, flags=re.DOTALL):
            if f'"{keyword}"' in raw_match or f'\\"{keyword}\\"' in raw_match:
                try:
                    parsed = self._parse_json(raw_match, video_id, fatal=False)
                    if parsed and len(parsed) >= 2 and isinstance(parsed[1], str):
                        content = parsed[1]
                        idx = content.find('[')
                        if idx != -1:
                            chunk = json.loads(content[idx:])
                            results.append(chunk)
                except Exception:
                    pass
        return results

    def _parse_archive(self, archive_data, video_id):
        if (record_count := len(archive_data.get('ivsRecords', []))) != 1:
            self.report_warning(f'Expected single ivsRecords, got {record_count}')
        m3u8_url = traverse_obj(archive_data, ('ivsRecords', 0, 'archiveUrl', {url_or_none}))
        if not m3u8_url:
            raise ExtractorError('無法取得 Withny 存檔串流網址 (可能需要購買或登入 Token 過期)', expected=True)
            
        for name, value in archive_data.get('cookies', {}).items():
            self._set_cookie('.withny.fun', name, value, discard=True)
            
        m3u8_headers = {'Referer': 'https://www.withny.fun/', 'Origin': 'https://www.withny.fun'}
        return {
            'id': video_id,
            'formats': self._extract_m3u8_formats(m3u8_url, video_id, headers=m3u8_headers),
            'age_limit': 18,
            'live_status': 'was_live',
            'http_headers': m3u8_headers,
            **traverse_obj(archive_data, {
                'title': ('title', {str}),
                'description': ('description', {str}, {clean_html}),
                'thumbnail': ('thumbnailUrl', {url_or_none}),
                'timestamp': ('createdAt', {parse_iso8601}),
                'uploader': ('cast', 'user', 'name'),
                'uploader_id': ('cast', 'user', 'username', {str}),
                'duration': ('ivsRecords', 0, 'recordingDurationMs', {lambda x: int_or_none(x, scale=1000)}),
            }),
        }


class WithnyVideoIE(WithnyBaseIE):
    _VALID_URL = r'https?://(?:www\.)?withny\.fun/(?:user/)?archives/(?P<id>[\d\w\-]+)'
    _TESTS = [{
        'url': 'https://www.withny.fun/user/archives/463019c0-3c34-494e-a3b3-ea6546ee63ac',
        'info_dict': {
            'id': '463019c0-3c34-494e-a3b3-ea6546ee63ac',
            'ext': 'mp4',
            'title': 'md5:d15ae047797fba83617af139aa0eeca2',
            'description': 'md5:91d282a920eda4dc84184e16a6957f26',
            'uploader': '桜彗ふらち',
            'uploader_id': 'OuseFurachi',
            'duration': 4032,
            'thumbnail': r're:https://.*',
            'timestamp': 1723990247,
            'upload_date': '20240818',
            'age_limit': 18,
            'live_status': 'was_live',
        },
    }]

    def _real_extract(self, url):
        video_id = self._match_id(url)
        self._setup_credentials()

        webpage = self._download_webpage(f'https://www.withny.fun/user/archives/{video_id}', video_id)
        segs = self._search_next_seg('archiveData', webpage, video_id)
        archive_data = traverse_obj(segs, (
            ..., ..., lambda _, v: isinstance(v, dict) and 'archiveData' in v, 'archiveData', {dict}, any))
        if not archive_data:
            archive_data = traverse_obj(segs, (..., 3, 'archiveData', {dict}, any))
        if not archive_data:
            archive_data = traverse_obj(segs, (..., ..., 'children', ..., 'archiveData', {dict}, any))
        if not archive_data:
            raise ExtractorError('無法解析 Withny 存檔資料 (Failed to find archive data)')
        return self._parse_archive(archive_data, video_id)


class WithnyPurchaseListIE(WithnyBaseIE):
    _VALID_URL = r'https?://(?:www\.)?withny\.fun/user/(?P<id>archives)/?(?:[?#]|$)'
    _TESTS = [{
        'url': 'https://www.withny.fun/user/archives',
        'info_dict': {
            'id': 'archives',
        },
        'playlist_mincount': 1,
    }]

    def _real_extract(self, url):
        self._setup_credentials()

        def _entries():
            page_size = 1
            for page in itertools.count(1):
                webpage = self._download_webpage(url, 'archive', query={'page': page})
                segs = self._search_next_seg('initialArchives', webpage, f'page-{page}')
                archives = traverse_obj(segs, (
                    ..., ..., lambda _, v: isinstance(v, dict) and 'initialArchives' in v, 'initialArchives', {dict}, any))
                if not archives:
                    archives = traverse_obj(segs, (..., 3, 'initialArchives', {dict}, any))
                if not archives or not archives.get('data'):
                    break
                for item in traverse_obj(archives, ('data', ..., {
                    'id': ('uuid', {str}),
                    'title': ('title', {str}),
                })):
                    yield self.url_result(f'https://www.withny.fun/user/archives/{item["id"]}', WithnyVideoIE, **item)
                page_size = max(page_size, len(archives['data']))
                if page * page_size >= archives.get('count', 0):
                    break
        return self.playlist_result(_entries(), 'archives')


class WithnyLiveIE(WithnyBaseIE):
    _VALID_URL = r'https?://(?:www\.)?withny\.fun/channels/(?P<id>[\d\w\-]+)'
    _TESTS = [{
        'url': 'https://www.withny.fun/channels/nekonametuna',
        'only_matching': True,
    }, {
        'url': 'https://www.withny.fun/channels/mikuru',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        user_id = self._match_id(url)
        self._setup_credentials()

        webpage = self._download_webpage(url, user_id)
        cast_candidates = self._search_next_seg('initialCast', webpage, user_id)
        channel_data = traverse_obj(cast_candidates, (
            ..., ..., lambda _, v: isinstance(v, dict) and 'initialCast' in v, 'initialCast', {dict}, any))
        if not channel_data:
            channel_data = traverse_obj(cast_candidates, (..., 3, 'initialCast', {dict}, any))
        if not channel_data:
            channel_data = traverse_obj(cast_candidates, (
                ..., ..., 'children', ..., ..., 'initialCast', {dict}, any))
            
        if not channel_data or 'ivsChannel' not in channel_data:
            raise ExtractorError(f'無法解析 Withny 頻道資料: {user_id}')
            
        channel_id = channel_data['ivsChannel']['uuid']
        if (live_status := channel_data['ivsChannel'].get('state')) != 'live':
            if not self._downloader.params.get('wait_for_video'):
                raise UserNotLive(f'頻道目前未開台 (Channel is not live: {live_status})')

        token = traverse_obj(self._search_next_seg('accessToken', webpage, user_id), (
            ..., ..., lambda _, v: isinstance(v, dict) and 'accessToken' in v, 'accessToken', {str}, any))
            
        if not token:
            session_info = self._download_json(
                'https://www.withny.fun/api/auth/session', user_id,
                note='Retrieving accessToken from Withny session', fatal=False)
            if session_info and isinstance(session_info, dict):
                token = session_info.get('accessToken')
                if not token and session_info.get('error') == 'RefreshAccessTokenError':
                    self.report_warning('Withny Session Token 已於伺服器端過期失效 (RefreshAccessTokenError)')

        if not token and self._withny_token:
            token = self._withny_token

        if not token:
            self.raise_login_required('需要有效的 Withny 登入 Token 才能獲取直播串流 (請在瀏覽器重新整理 Withny 頁面並同步最新 Token)')

        ws_url = f'wss://api.withny.fun/socket.io/?uuid={channel_id}&token={token}&passCode=undefined&EIO=4&transport=websocket'
        self.to_screen(f'{user_id}: Fetching stream info via WebSocket')
        
        stream_data = None
        try:
            import websocket
            ws = websocket.create_connection(
                ws_url,
                header={'Origin': 'https://www.withny.fun', 'User-Agent': 'Mozilla/5.0'},
                timeout=15)
            # Handshake
            ws.recv()
            session_id = ''.join(random.choices('0123456789abcdef', k=16))
            ws.send(f'40/channels,{{"sessionID":"{session_id}"}}')
            
            start_wait = time.time()
            while time.time() - start_wait < 30:
                msg = ws.recv()
                if isinstance(msg, str):
                    if 'token is invalid' in msg or 'Forbidden' in msg:
                        ws.close()
                        self.raise_login_required(f'Invalid login info: {msg}')

                    if msg.startswith('42/channels,["stream"'):
                        stream_data = json.loads(msg.split(',', maxsplit=1)[1])[1]
                        break
                    elif msg == '2':
                        ws.send('3')  # heartbeat
                    elif 'changeNumOfStandby' in msg:
                        if self._downloader.params.get('wait_for_video'):
                            self.to_screen(f'{user_id}: channel is on standby')
                        else:
                            ws.close()
                            raise UserNotLive('頻道處於待機狀態 (standby)')
                    elif 'streamStart' in msg:
                        ws.close()
                        return self._real_extract(url)
            ws.close()
        except Exception as e:
            if isinstance(e, (ExtractorError, UserNotLive)):
                raise
            self.report_warning(f'WebSocket error: {e}')

        if not stream_data or 'uuid' not in stream_data:
            raise ExtractorError(f'無法透過 WebSocket 取得直播 stream 資料 ({user_id})')

        stream_id = stream_data['uuid']
        playback_data = self._download_json(
            f'https://www.withny.fun/api/streams/{stream_id}/playback-url', user_id,
            headers={
                'Authorization': f'Bearer {token}',
                'Referer': 'https://www.withny.fun/',
                'Origin': 'https://www.withny.fun',
            })
        m3u8_url = playback_data if isinstance(playback_data, str) else (playback_data.get('playbackUrl') or playback_data.get('url'))
        if not m3u8_url:
            raise ExtractorError('無法獲取 Withny 直播 m3u8 串流網址')
            
        m3u8_headers = {'Referer': 'https://www.withny.fun/', 'Origin': 'https://www.withny.fun'}

        return {
            'id': stream_id,
            'formats': self._extract_m3u8_formats(m3u8_url, user_id, headers=m3u8_headers, live=True),
            'age_limit': 18,
            'live_status': 'is_live',
            'http_headers': m3u8_headers,
            **traverse_obj(stream_data, {
                'title': ('title', {str}),
                'description': ('about', {str}, {clean_html}),
                'timestamp': ('startedAt', {parse_iso8601}),
                'thumbnail': ('thumbnailUrl', {url_or_none}),
            }),
            **traverse_obj(channel_data, {
                'uploader': ('user', 'name', {str}),
                'uploader_id': ('user', 'username', {str}),
            }),
        }
