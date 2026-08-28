// ==UserScript==
// @name         StreamBot Withny Token iOS/Safari 專用同步器
// @namespace    https://withny.fun/
// @version      3.0
// @description  專為 iOS Safari (Stay / Userscripts) 優化，一鍵擷取與同步 Withny Token 至電腦 StreamBot
// @author       Antigravity
// @match        https://withny.fun/*
// @match        https://*.withny.fun/*
// @grant        GM_xmlhttpRequest
// @run-at       document-end
// ==/UserScript==

(function() {
    'use strict';

    // 電腦在區域網路 (Wi-Fi) 中的 IP 位址 (請點擊電腦 StreamBot 設定頁「複製 Wi-Fi 跨裝置同步位址」)
    const SYNC_URL = 'http://192.168.68.110:18730/update_withny_token';
    const TOKEN_REGEX = /eyJ[A-Za-z0-9_-]+\.\.?[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g;

    async function extractMobileWithnyToken() {
        const candidateTokens = [];

        // 1. 檢視 iOS Safari 頁面 DOM 中的 __NEXT_DATA__
        try {
            const nextData = document.getElementById('__NEXT_DATA__');
            if (nextData && nextData.textContent) {
                const matches = nextData.textContent.match(TOKEN_REGEX);
                if (matches) candidateTokens.push(...matches);
            }
        } catch (e) {}

        // 2. 檢視 document.cookie (iOS Safari Stay / Userscripts 可讀取之 Cookie)
        try {
            const cookies = document.cookie.split(';');
            const chunks = [];
            for (let c of cookies) {
                const parts = c.trim().split('=');
                if (parts.length >= 2) {
                    const name = parts[0].trim();
                    const val = decodeURIComponent(parts.slice(1).join('=')).trim().replace(/^["']|["']$/g, '');
                    if (val.startsWith('eyJ')) candidateTokens.push(val);
                    if (name.includes('session-token')) {
                        const m = name.match(/\.(\d+)$/);
                        if (m) chunks.push({ idx: parseInt(m[1], 10), val: val });
                    }
                }
            }
            if (chunks.length > 0) {
                chunks.sort((a, b) => a.idx - b.idx);
                const merged = chunks.map(c => c.val).join('');
                if (merged.startsWith('eyJ')) candidateTokens.unshift(merged);
            }
        } catch (e) {}

        // 3. 檢視 LocalStorage 與 SessionStorage
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const v = localStorage.getItem(localStorage.key(i));
                if (typeof v === 'string') {
                    const cleanV = v.trim().replace(/^["']|["']$/g, '');
                    if (cleanV.startsWith('eyJ')) candidateTokens.push(cleanV);
                }
            }
        } catch (e) {}

        // 挑選長度最長且符合 eyJ 格式的 Token
        let bestToken = null;
        for (let tok of candidateTokens) {
            if (tok && tok.startsWith('eyJ') && tok.length > 50) {
                if (!bestToken || tok.length > bestToken.length) {
                    bestToken = tok;
                }
            }
        }
        return bestToken;
    }

    async function syncToken(isManual = false) {
        let token = await extractMobileWithnyToken();

        // 若自動讀取失敗且為手動點擊，彈出提示引導
        if (!token && isManual) {
            const inputToken = prompt(
                '🪐 請輸入或貼入您的 Withny Session Token (以 eyJ 開頭)：',
                ''
            );
            if (inputToken && inputToken.trim()) {
                token = inputToken.trim();
            }
        }

        if (!token) {
            if (isManual) alert('❌ 未找到有效的 Withny Session Token，請確認您已在 Safari 登入 withny.fun！');
            return;
        }

        const data = JSON.stringify({ token: token, platform: 'withny' });

        const handleSuccess = () => {
            console.log('[StreamBot Mobile Withny Sync] 同步成功');
            if (isManual) alert('✅ 已成功將手機 Withny Token 同步至電腦 StreamBot！');
        };

        const handleError = () => {
            console.log('[StreamBot Mobile Withny Sync] 同步失敗');
            if (isManual) alert('❌ 同步失敗！請確認電腦 StreamBot 已開啟，且手機與電腦連接在同一個 Wi-Fi 網路。');
        };

        if (typeof GM_xmlhttpRequest !== 'undefined') {
            GM_xmlhttpRequest({
                method: 'POST',
                url: SYNC_URL,
                headers: { 'Content-Type': 'application/json' },
                data: data,
                onload: (res) => {
                    if (res.status === 200) handleSuccess();
                    else handleError();
                },
                onerror: handleError
            });
        } else {
            fetch(SYNC_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: data
            }).then(r => {
                if (r.ok) handleSuccess();
                else handleError();
            }).catch(handleError);
        }
    }

    function createUI() {
        if (document.getElementById('streambot-mobile-withny-btn')) return;

        const btn = document.createElement('button');
        btn.id = 'streambot-mobile-withny-btn';
        btn.innerHTML = '🪐 同步到電腦';
        btn.style.cssText = `
            position: fixed;
            bottom: 30px;
            right: 20px;
            z-index: 999999;
            padding: 12px 18px;
            background: #cba6f7;
            color: #11111b;
            font-size: 14px;
            font-weight: bold;
            font-family: -apple-system, sans-serif;
            border: none;
            border-radius: 25px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.4);
            cursor: pointer;
        `;

        btn.onclick = () => syncToken(true);
        document.body.appendChild(btn);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', createUI);
    } else {
        createUI();
    }

    setTimeout(() => syncToken(false), 2000);
})();
