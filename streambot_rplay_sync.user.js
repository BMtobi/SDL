// ==UserScript==
// @name         StreamBot Rplay Token 自動與手動同步器
// @namespace    https://rplay.live/
// @version      1.2
// @description  自動與手動同步 Rplay 最新 Token 到 StreamBot 本機服務
// @author       Antigravity
// @match        https://rplay.live/*
// @match        https://*.rplay.live/*
// @grant        GM_xmlhttpRequest
// @run-at       document-end
// ==/UserScript==

(function() {
    'use strict';

    const SYNC_URL = 'http://127.0.0.1:18730/update_token';

    function syncToken(isManual = false) {
        const token = localStorage.getItem('_AUTHORIZATION_');
        if (!token) {
            if (isManual) alert('❌ 未在 localStorage 找到 Rplay Token (_AUTHORIZATION_)，請先登入帳號！');
            return;
        }

        const data = JSON.stringify({ token: token });

        const handleSuccess = () => {
            console.log('[StreamBot Sync] Token 同步成功');
            if (isManual) {
                showToast('✅ 已成功手動同步 Token 至 StreamBot！');
            }
        };

        const handleError = () => {
            console.log('[StreamBot Sync] 同步失敗');
            if (isManual) {
                alert('❌ 手動同步失敗！請確認 StreamBot 已啟動，且連接埠 18730 正常運行。');
            }
        };

        if (typeof GM_xmlhttpRequest !== 'undefined') {
            GM_xmlhttpRequest({
                method: 'POST',
                url: SYNC_URL,
                headers: { 'Content-Type': 'application/json' },
                data: data,
                onload: function(res) {
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
        if (document.getElementById('streambot-sync-btn')) return;

        const btn = document.createElement('button');
        btn.id = 'streambot-sync-btn';
        btn.innerHTML = '🔄 同步 Token 到 StreamBot';
        btn.style.cssText = `
            position: fixed;
            bottom: 25px;
            right: 25px;
            z-index: 999999;
            padding: 10px 16px;
            background: #89b4fa;
            color: #11111b;
            font-size: 13px;
            font-weight: bold;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            border: none;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.35);
            cursor: pointer;
            transition: all 0.2s ease;
        `;

        btn.onmouseover = () => btn.style.background = '#b4befe';
        btn.onmouseout = () => btn.style.background = '#89b4fa';
        btn.onclick = () => syncToken(true);

        document.body.appendChild(btn);
    }

    function showToast(message) {
        const toast = document.createElement('div');
        toast.innerText = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 70px;
            right: 25px;
            z-index: 999999;
            padding: 10px 16px;
            background: #a6e3a1;
            color: #11111b;
            font-size: 13px;
            font-weight: bold;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            border-radius: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            transition: opacity 0.3s;
        `;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 2500);
    }

    // 建立手動按鈕
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', createUI);
    } else {
        createUI();
    }

    // 頁面載入 2 秒後背景自動同步
    setTimeout(() => syncToken(false), 2000);

    // 每 3 分鐘自動背景同步
    setInterval(() => syncToken(false), 180000);
})();
