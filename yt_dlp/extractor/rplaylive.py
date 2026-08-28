import base64
import json
import random
import re
import time

from .common import InfoExtractor
from ..aes import aes_cbc_encrypt_bytes
from ..utils import (
    ExtractorError,
    UserNotLive,
    encode_data_uri,
    float_or_none,
    jwt_decode_hs256,
    parse_iso8601,
    parse_qs,
    traverse_obj,
    url_or_none,
)


class RPlayBaseIE(InfoExtractor):
    _NETRC_MACHINE = 'rplaylive'
    _user_id = None
    _refresh_token = None
    _jwt_token = None

    def _setup_credentials(self):
        try:
            for p in ['settings.json', '../settings.json', 'C:/AI pro/grind code/StreamBot/settings.json']:
                if os.path.exists(p):
                    with open(p, 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                        if tok := cfg.get('rplay_token'):
                            self._jwt_token = tok.strip()
                        if u_id := cfg.get('rplay_username'):
                            self._user_id = u_id.strip()
                        break
        except Exception:
            pass

        headers = self._downloader.params.get('http_headers', {}) if hasattr(self, '_downloader') and self._downloader else {}
        if not self._jwt_token:
            if 'Authorization' in headers and headers['Authorization'] and headers['Authorization'] != 'null':
                self._jwt_token = headers['Authorization']
            jwt_arg = self._configuration_arg('jwt_token', ie_key='rplaylive', casesense=True)
            if jwt_arg:
                self._jwt_token = jwt_arg[0]

        if not self._user_id:
            if 'rplay-private-content-requestor' in headers and headers['rplay-private-content-requestor']:
                self._user_id = headers['rplay-private-content-requestor']
            user_arg = self._configuration_arg('user_id', ie_key='rplaylive', casesense=True)
            if user_arg:
                self._user_id = user_arg[0]

    @property
    def user_id(self):
        self._setup_credentials()
        return self._user_id

    @property
    def jwt_token(self):
        self._setup_credentials()
        return self._jwt_token

    @property
    def requestor_query(self):
        return {
            'requestorOid': self.user_id,
            'loginType': 'rplay',
        } if self.user_id else {}

    @property
    def refresh_token_header(self):
        return {
            'Origin': 'https://rplay.live',
            'Referer': 'https://rplay.live/',
            'Platform-Type': 'rplay',
            **({'Refresh-Token': self._refresh_token} if self._refresh_token else {}),
        }

    @property
    def jwt_header(self):
        headers = {
            'Referer': 'https://rplay.live/',
            'Origin': 'https://rplay.live',
            'Platform-Type': 'rplay',
            'Butter': self.get_butter_token(),
            **self.refresh_token_header,
            'Authorization': self.jwt_token or 'null',
        }
        if self.user_id:
            headers['rplay-private-content-requestor'] = self.user_id
        return headers

    @property
    def butter_header(self):
        return {
            'Referer': 'https://rplay.live/',
            'Origin': 'https://rplay.live',
            'Platform-Type': 'rplay',
            'Butter': self.get_butter_token(),
        }

    def _login_hint(self, *args, **kwargs):
        return (f'Use --username and --password, --netrc-cmd, --netrc ({self._NETRC_MACHINE}) '
                'to provide account credentials. For third-party login, use --username <requestorOid> '
                '--password <refresh-token> to pass credential (find oid in query and token in header).')

    def _perform_login(self, username, password):
        if '@' in username:
            result = self._download_json(
                'https://api.rplay.live/rplay/account/login', 'login', note='performing email login',
                data=json.dumps({'accountType': 'plax', 'email': username, 'password': password}).encode(),
                headers={'Content-Type': 'application/json'}, fatal=False)
            if traverse_obj(result, 'success'):
                self._refresh_token = result['refreshToken']
                self._jwt_token = result['token']
                self._user_id = result['user']['_id']
            else:
                self.report_warning('Failed to login using email password')
        elif re.match(r'[0-9a-f]{24}', username):
            self._user_id = username
            self._refresh_token = password
            self._perform_refresh()
            if not self._jwt_token:
                self.report_warning('Invalid refresh token, make sure you take requestorOid (NOT creatorOid) and '
                                    'refresh-token (NOT authorization JWT token)')
        else:
            self.report_warning('only email + password, or requestorOid + refresh-token can be used for login')

    def _perform_refresh(self):
        result = self._download_json(
            'https://api.rplay.live/rplay/account/refresh-token', 'refresh', note='refreshing token',
            data=json.dumps({'requestorOid': self._user_id}).encode(),
            headers={'Content-Type': 'application/json', **self.refresh_token_header}, fatal=False)
        if jwt := traverse_obj(result, ('accessToken', {str})):
            self._jwt_token = jwt
        else:
            self.report_warning('Failed to refresh jwt token')

    def get_butter_token(self):
        salt = b'QWI@(!WAS)Dj1AA(!@*DJ#@$@~1)P'
        key = b'S%M@#H#B(!@()a2@'
        ts_value = bytes(f'{int(time.time() / 360)}', 'utf-8')
        enc = aes_cbc_encrypt_bytes(salt + b'https://rplay.live' + ts_value, key, ts_value.zfill(16))
        return enc.hex()


class RPlayVideoIE(RPlayBaseIE):
    _VALID_URL = r'https://rplay\.live/play/(?P<id>[\d\w]+)'
    _TESTS = [{
        'url': 'https://rplay.live/play/669203d25223214e67579dc3/',
        'info_dict': {
            'id': '669203d25223214e67579dc3',
            'ext': 'mp4',
            'title': 'md5:6ab0a76410b40b1f5fb48a2ad7571264',
            'description': 'md5:d2fb2f74a623be439cf454df5ff3344a',
            'timestamp': 1720845266,
            'upload_date': '20240713',
            'release_timestamp': 1720846360,
            'release_date': '20240713',
            'duration': 5349.0,
            'thumbnail': r're:https://[\w\d]+.cloudfront.net/.*',
            'uploader': '杏都める',
            'uploader_id': '667adc9e9aa7f739a2158ff3',
            'tags': ['杏都める', 'めいどるーちぇ', '無料', '耳舐め', 'ASMR'],
        },
    }]

    def _real_extract(self, url):
        video_id = self._match_id(url)
        self._setup_credentials()

        playlist_id = traverse_obj(parse_qs(url), ('playlist', ..., any))
        if playlist_id and self._yes_playlist(playlist_id, video_id):
            playlist_info = self._download_json(
                'https://api.rplay.live/content/playlist', playlist_id,
                query={'playlistOid': playlist_id},
                headers=self.jwt_header, fatal=False)
            if playlist_info:
                entries = traverse_obj(playlist_info, ('contentData', ..., '_id', {
                    lambda x: self.url_result(f'https://rplay.live/play/{x}/', ie=RPlayVideoIE, video_id=x)}))
                return self.playlist_result(entries, playlist_id, playlist_info.get('name'))
            else:
                self.report_warning('Failed to get playlist, downloading video only')

        video_info = self._download_json('https://api.rplay.live/content', video_id, query={
            'contentOid': video_id,
            'status': 'published',
            'withComments': True,
            'requestCanView': True,
        }, headers=self.jwt_header, fatal=False, errnote=False)

        if not video_info or not isinstance(video_info, dict) or not video_info.get('title'):
            # Fallback without auth query if expired token caused 500 error
            video_info = self._download_json('https://api.rplay.live/content', video_id, query={
                'contentOid': video_id,
                'status': 'published',
                'withComments': True,
                'requestCanView': True,
            }, headers=self.butter_header)
        if video_info.get('drm'):
            raise ExtractorError('This video is DRM-protected')

        metainfo = traverse_obj(video_info, {
            'title': ('title', {str}),
            'description': ('introText', {str}),
            'release_timestamp': ('publishedAt', {parse_iso8601}),
            'timestamp': ('createdAt', {parse_iso8601}),
            'duration': ('length', {float_or_none}),
            'uploader': ('nickname', {str}),
            'uploader_id': ('creatorOid', {str}),
            'tags': ('hashtags', lambda _, v: v[0] != '_'),
            'age_limit': (('hideContent', 'isAdultContent'), {lambda x: 18 if x else None}, any),
            'location': ('location', {str}),
            'view_count': ('views', {int}),
            'like_count': ('likes', {int}),
            'live_status': ('isReplayContent', {lambda x: 'was_live' if x else None}),
            'thumbnail': ('thumbnail', {url_or_none}),
        })

        if video_info.get('isUseMultiTitle'):
            translated_metainfo = traverse_obj(video_info, ('multiLangTitle', self._get_cookie_lang(), {
                'title': ('title', {str}),
                'description': ('introText', {str}),
            }))
            metainfo.update(translated_metainfo)

        m3u8_url = traverse_obj(video_info, ('canView', 'url', {url_or_none}))
        if not m3u8_url:
            msg = 'RPLAY_AUTH_ERROR: You do not have access to this video (Token可能已過期或帳號無此影片權限)'
            if traverse_obj(video_info, ('viewableTiers', 'free')):
                msg = 'RPLAY_AUTH_ERROR: This video requires a free subscription to access'
            if not self.user_id:
                msg += f'. {self._login_hint(method="password")}'
            raise ExtractorError(msg, expected=True)

        thumbnail_key = traverse_obj(video_info, (
            'streamables', lambda _, v: v['type'].startswith('image/'), 's3key', any))
        if thumbnail_key:
            metainfo['thumbnail'] = url_or_none(self._download_webpage(
                'https://api.rplay.live/upload/privateasset', video_id, 'getting cover url', query={
                    'key': thumbnail_key,
                    'contentOid': video_id,
                    'creatorOid': metainfo.get('uploader_id'),
                }, headers=self.jwt_header, fatal=False))

        formats = self._extract_m3u8_formats(m3u8_url, video_id, headers=self.jwt_header, fatal=False)
        if not formats:
            raise ExtractorError('No video formats found or cannot extract m3u8 playlist', expected=True)
            
        for fmt in formats:
            try:
                m3u8_doc = self._download_webpage(fmt['url'], video_id, 'getting m3u8 contents', headers=self.jwt_header, fatal=False)
                if not m3u8_doc:
                    continue
                fmt['url'] = encode_data_uri(m3u8_doc.encode(), 'application/x-mpegurl')
                match = re.search(r'^#EXT-X-KEY.*?URI="([^"]+)"', m3u8_doc, flags=re.M)
                if match:
                    urlh = self._request_webpage(match[1], video_id, 'getting hls key', headers=self.jwt_header, fatal=False)
                    if urlh:
                        fmt['hls_aes'] = {'key': urlh.read().hex()}
            except Exception as e:
                self.report_warning(f'Could not process format {fmt.get("format_id")}: {e}', video_id)

        return {
            'id': video_id,
            'formats': formats,
            **metainfo,
            'http_headers': self.jwt_header,
        }


class RPlayUserIE(InfoExtractor):
    _VALID_URL = r'https://rplay\.live/(?P<short>c|creatorhome)/(?P<id>[\d\w]+)/?(?:[#?]|$)'
    _TESTS = [{
        'url': 'https://rplay.live/creatorhome/667adc9e9aa7f739a2158ff3?page=contents',
        'info_dict': {
            'id': '667adc9e9aa7f739a2158ff3',
            'title': '杏都める',
        },
        'playlist_mincount': 34,
    }, {
        'url': 'https://rplay.live/c/furachi?page=contents',
        'info_dict': {
            'id': '65e07e60850f4527aab74757',
            'title': '逢瀬ふらち OuseFurachi',
        },
        'playlist_mincount': 77,
    }]

    def _real_extract(self, url):
        user_id, short = self._match_valid_url(url).group('id', 'short')

        user_info = self._download_json('https://api.rplay.live/account/getuser', user_id, query={
            'customUrl' if short == 'c' else 'userOid': user_id, 'options': '{"includeContentMetadata":true}'})
        replays = self._download_json(
            'https://api.rplay.live/live/replays', user_id, query={'creatorOid': user_info.get('_id')})

        def _entries():
            def _entry_ids():
                for entry_id in traverse_obj(user_info, ('published', ..., {str})):
                    yield entry_id
                for entry_id in traverse_obj(replays, (..., '_id', {str})):
                    if entry_id not in user_info.get('published', []):
                        yield entry_id

            for entry_id in _entry_ids():
                yield self.url_result(f'https://rplay.live/play/{entry_id}/', ie=RPlayVideoIE, video_id=entry_id)

        return self.playlist_result(_entries(), user_info.get('_id', user_id), user_info.get('nickname'))


class RPlayLiveIE(RPlayBaseIE):
    _VALID_URL = [
        r'https://rplay\.live/(?P<short>c)/(?P<id>[\d\w]+)/live',
        r'https://rplay\.live/(?P<short>live)/(?P<id>[\d\w]+)',
    ]
    _TESTS = [{
        'url': 'https://rplay.live/c/chachamaru/live',
        'info_dict': {
            'id': '667e4cd99aa7f739a2c91852',
            'ext': 'mp4',
            'title': r're:【ASMR】ん～っやば//スキスキ耐久.*',
            'description': 'md5:7f88ac0a7a3d5d0b926a0baecd1d40e1',
            'timestamp': 1721739947,
            'upload_date': '20240723',
            'live_status': 'is_live',
            'thumbnail': 'https://pb.rplay.live/liveChannelThumbnails/667e4cd99aa7f739a2c91852',
            'uploader': '愛犬茶々丸',
            'uploader_id': '667e4cd99aa7f739a2c91852',
            'tags': 'count:9',
        },
        'skip': 'live',
    }]

    def _real_extract(self, url):
        user_id, short = self._match_valid_url(url).group('id', 'short')

        user_info = self._download_json('https://api.rplay.live/account/getuser', user_id, query={
            'customUrl' if short == 'c' else 'userOid': user_id})
        user_id = user_info['_id']

        live_info = self._download_json('https://api.rplay.live/live/play', user_id, query={
            'creatorOid': user_id, **self.requestor_query}, headers=self.jwt_header)

        stream_state = live_info['streamState']
        if stream_state == 'youtube':
            return self.url_result(f'https://www.youtube.com/watch?v={live_info["liveStreamId"]}')
        elif stream_state == 'live':
            if not self.user_id and not live_info.get('allowAnonymous'):
                self.raise_login_required(method='password')
            key2 = traverse_obj(self._download_json(
                'https://api.rplay.live/live/key2', user_id, 'getting live key',
                headers=self.jwt_header, query=self.requestor_query), ('authKey', {str})) if self.user_id else ''
            if key2 is None:
                raise ExtractorError('Failed to get playlist key')
            formats = self._extract_m3u8_formats(
                'https://api.rplay.live/live/stream/playlist.m3u8', user_id,
                query={'creatorOid': user_id, 'key2': key2}, headers={'Referer': 'https://rplay.live'})

            return {
                'id': user_id,
                'formats': formats,
                'is_live': True,
                'http_headers': {'Referer': 'https://rplay.live'},
                'thumbnail': f'https://pb.rplay.live/liveChannelThumbnails/{user_id}',
                'uploader': traverse_obj(user_info, ('nickname', {str})),
                'uploader_id': user_id,
                **traverse_obj(live_info, {
                    'title': ('title', {str}),
                    'description': ('description', {str}),
                    'timestamp': ('streamStartTime', {parse_iso8601}),
                    'tags': ('hashtags', ..., {str}),
                    'age_limit': ('isAdultContent', {lambda x: 18 if x else None}),
                }),
            }
        elif stream_state == 'offline':
            raise UserNotLive
        else:
            raise ExtractorError(f'Unknow streamState: {stream_state}')
