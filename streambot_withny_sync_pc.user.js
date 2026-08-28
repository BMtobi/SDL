// ==UserScript==
// @name         StreamBot Withny Token 無感自動攔截同步器 (PC版)
// @namespace    https://withny.fun/
// @version      2.0
// @description  自動攔截與解析 Withny 網路連線及頁面資料中的 Session Token 並零操作同步至 StreamBot
// @author       Antigravity
// @match        https://withny.fun/*
// @match        https://*.withny.fun/*
// @grant        GM_cookie
// @grant        GM_xmlhttpRequest
// @run-at       document-start
// ==/UserScript==

(function() {
    'use strict';

    const SYNC_URL = 'http://127.0.0.1:18730/update_withny_token';
    const TOKEN_REGEX = /eyJ[A-Za-z0-9_-]+\.\.?[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g;

    let lastSyncedToken = '';

    function autoSync(token, source = '') {
        if (!token || typeof token !== 'string') return;
        const cleanToken = token.trim().replace(/^["']|["']$/g, '');
        if (!cleanToken.startsWith('eyJ') || cleanToken.length < 50) return;

        if (cleanToken === lastSyncedToken) return;
        lastSyncedToken = cleanToken;

        console.log(`[StreamBot Withny Interceptor] 成功自動捕捉 Token (${source})，準備同步...`);

        const data = JSON.stringify({ token: cleanToken, platform: 'withny' });

        if (typeof GM_xmlhttpRequest !== 'undefined') {
            GM_xmlhttpRequest({
                method: 'POST',
                url: SYNC_URL,
                headers: { 'Content-Type': 'application/json' },
                data: data,
                onload: (res) => {
                    if (res.status === 200) showToast('✅ 瀏覽器已自動連線並同步 Withny Token 至 StreamBot！');
                }
            });
        } else {
            fetch(SYNC_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: data
            }).then(r => {
                if (r.ok) showToast('✅ 瀏覽器已自動連線並同步 Withny Token 至 StreamBot！');
            }).catch(() => {});
        }
    }

    // 1. 攔截演算法: 網路請求 Fetch 攔截器
    const origFetch = window.fetch;
    window.fetch = async function(...args) {
        const response = await origFetch.apply(this, args);
        try {
            const clone = response.clone();
            const text = await clone.text();
            if (text && text.includes('eyJ')) {
                const matches = text.match(TOKEN_REGEX);
                if (matches && matches.length > 0) {
                    autoSync(matches[0], 'Fetch Response');
                }
            }
        } catch (e) {}
        return response;
    };

    // 2. 攔截演算法: 網路請求 XHR 攔截器
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function() {
        this.addEventListener('load', function() {
            try {
                if (this.responseText && this.responseText.includes('eyJ')) {
                    const matches = this.responseText.match(TOKEN_REGEX);
                    if (matches && matches.length > 0) {
                        autoSync(matches[0], 'XHR Response');
                    }
                }
            } catch (e) {}
        });
        origOpen.apply(this, arguments);
    };

    // 3. 掃描 Next.js 頁面 DOM 狀態 (__NEXT_DATA__)
    function scanDOM() {
        try {
            const nextData = document.getElementById('__NEXT_DATA__');
            if (nextData && nextData.textContent) {
                const matches = nextData.textContent.match(TOKEN_REGEX);
                if (matches && matches.length > 0) {
                    autoSync(matches[0], '__NEXT_DATA__');
                    return;
                }
            }
            const bodyText = document.documentElement.innerHTML;
            if (bodyText && bodyText.includes('eyJ')) {
                const matches = bodyText.match(TOKEN_REGEX);
                if (matches && matches.length > 0) {
                    autoSync(matches[0], 'HTML Body');
                }
            }
        } catch (e) {}
    }

    // 4. 掃描 Cookie 與 LocalStorage
    async function scanStorage() {
        if (typeof GM_cookie !== 'undefined' && GM_cookie.list) {
            const targets = [{ url: window.location.href }, { url: 'https://withny.fun' }, { domain: 'withny.fun' }];
            for (const t of targets) {
                try {
                    const cookies = await new Promise(res => GM_cookie.list(t, (r) => res(r || [])));
                    const chunks = [];
                    for (const c of cookies) {
                        const val = decodeURIComponent(c.value || '').trim();
                        if (val.startsWith('eyJ')) autoSync(val, 'GM_cookie');
                        if (c.name && c.name.includes('session-token')) {
                            const m = c.name.match(/\.(\d+)$/);
                            if (m) chunks.push({ idx: parseInt(m[1], 10), val: val });
                        }
                    }
                    if (chunks.length > 0) {
                        chunks.sort((a, b) => a.idx - b.idx);
                        autoSync(chunks.map(c => c.val).join(''), 'GM_cookie_chunked');
                    }
                } catch (e) {}
            }
        }

        for (let i = 0; i < localStorage.length; i++) {
            const v = localStorage.getItem(localStorage.key(i));
            if (typeof v === 'string' && v.startsWith('eyJ')) {
                autoSync(v, 'LocalStorage');
            }
        }
    }

    function showToast(message) {
        if (document.getElementById('streambot-toast')) return;
        const toast = document.createElement('div');
        toast.id = 'streambot-toast';
        toast.innerText = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 25px;
            right: 25px;
            z-index: 999999;
            padding: 12px 18px;
            background: #a6e3a1;
            color: #11111b;
            font-size: 13px;
            font-weight: bold;
            font-family: -apple-system, sans-serif;
            border-radius: 8px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            transition: opacity 0.3s;
        `;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    document.addEventListener('DOMContentLoaded', () => {
        scanDOM();
        scanStorage();
    });

    setTimeout(() => { scanDOM(); scanStorage(); }, 1500);
    setInterval(scanStorage, 60000);
})();
