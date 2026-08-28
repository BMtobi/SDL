const STREAMBOT_SERVER = 'http://127.0.0.1:18730';

let lastWithnyToken = '';
let lastRplayToken = '';

// 1. 同步與抓取 Withny Token (使用原生 chrome.cookies API)
async function checkAndSyncWithnyToken(forceSync = false) {
  try {
    const cookies = await chrome.cookies.getAll({ domain: 'withny.fun' });
    if (!cookies || cookies.length === 0) return false;

    // 處理 NextAuth 分段 Cookie (.0, .1...)
    const chunks = [];
    let singleToken = '';

    for (const c of cookies) {
      if (c.name.includes('session-token')) {
        const m = c.name.match(/\.(\d+)$/);
        if (m) {
          chunks.push({ idx: parseInt(m[1], 10), val: decodeURIComponent(c.value) });
        } else {
          singleToken = decodeURIComponent(c.value);
        }
      }
    }

    let fullToken = '';
    if (chunks.length > 0) {
      chunks.sort((a, b) => a.idx - b.idx);
      fullToken = chunks.map(c => c.val).join('');
    } else {
      fullToken = singleToken;
    }

    fullToken = fullToken.trim().replace(/^["']|["']$/g, '');

    if (fullToken && fullToken.startsWith('eyJ') && fullToken.length > 50) {
      if (forceSync || fullToken !== lastWithnyToken) {
        lastWithnyToken = fullToken;
        await sendToStreamBot('/update_withny_token', { token: fullToken, platform: 'withny' });
        console.log('[StreamBot Extension] 已自動擷取並同步最新 Withny Session Token');
      }
      return true;
    }
  } catch (e) {
    console.error('[StreamBot Extension] Withny Cookie 讀取失敗:', e);
  }
  return false;
}

// 切換至現有標籤頁或開啟新標籤頁
async function focusOrOpenUrl(urlPattern, targetUrl) {
  try {
    const tabs = await chrome.tabs.query({ url: urlPattern });
    if (tabs && tabs.length > 0) {
      const tab = tabs[0];
      await chrome.tabs.update(tab.id, { active: true });
      if (tab.windowId) {
        try {
          await chrome.windows.update(tab.windowId, { focused: true });
        } catch (e) {}
      }
      return tab;
    } else {
      const newTab = await chrome.tabs.create({ url: targetUrl, active: true });
      return newTab;
    }
  } catch (e) {
    console.warn('[StreamBot Extension] focusOrOpenUrl 失敗:', e);
  }
  return null;
}

// 2. 同步與抓取 Rplay Token (結合 Cookie、標籤頁切換與 LocalStorage)
async function checkAndSyncRplayToken(forceSync = false, autoNavigate = false) {
  let foundToken = '';

  // A. 嘗試從 Cookie 尋找
  try {
    const cookies = await chrome.cookies.getAll({ domain: 'rplay.live' });
    for (const c of cookies) {
      if (c.name === '_AUTHORIZATION_' || c.name.includes('authorization')) {
        const val = decodeURIComponent(c.value).trim().replace(/^["']|["']$/g, '');
        if (val && val.startsWith('eyJ')) {
          foundToken = val;
          break;
        }
      }
    }
  } catch (e) {}

  // B. 搜尋當前已開啟之 Rplay 標籤頁存取 localStorage
  if (!foundToken) {
    try {
      const tabs = await chrome.tabs.query({ url: '*://*.rplay.live/*' });
      if (tabs && tabs.length > 0) {
        for (const tab of tabs) {
          if (tab.id) {
            const results = await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              func: () => localStorage.getItem('_AUTHORIZATION_') || localStorage.getItem('rplay_token')
            });
            if (results && results[0] && results[0].result) {
              const tok = results[0].result.trim().replace(/^["']|["']$/g, '');
              if (tok && tok.startsWith('eyJ')) {
                foundToken = tok;
                break;
              }
            }
          }
        }
      }
    } catch (e) {}
  }

  // C. 若需要自動轉到/開啟 Rplay (autoNavigate)
  if (autoNavigate) {
    await focusOrOpenUrl('*://*.rplay.live/*', 'https://rplay.live/');
    // 等待標籤頁加載並嘗試再次提取
    await new Promise(r => setTimeout(r, 600));
    try {
      const tabs = await chrome.tabs.query({ url: '*://*.rplay.live/*' });
      for (const tab of tabs) {
        if (tab.id) {
          const results = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: () => localStorage.getItem('_AUTHORIZATION_') || localStorage.getItem('rplay_token')
          });
          if (results && results[0] && results[0].result) {
            const tok = results[0].result.trim().replace(/^["']|["']$/g, '');
            if (tok && tok.startsWith('eyJ')) {
              foundToken = tok;
              break;
            }
          }
        }
      }
    } catch (e) {}
  }

  if (foundToken) {
    if (forceSync || foundToken !== lastRplayToken) {
      lastRplayToken = foundToken;
      await sendToStreamBot('/update_token', { token: foundToken, platform: 'rplay' });
      console.log('[StreamBot Extension] 已自動擷取並同步最新 Rplay Token');
    }
    return true;
  }
  return false;
}

// HTTP 傳送至 StreamBot 本機服務
async function sendToStreamBot(endpoint, payload) {
  try {
    const res = await fetch(`${STREAMBOT_SERVER}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return res.ok;
  } catch (e) {
    console.warn('[StreamBot Extension] 無法連線至 StreamBot 服務 (18730)');
    return false;
  }
}

// 執行全自動同步
async function doAutoSync(forceSync = false, autoNavigate = false) {
  const withnyOk = await checkAndSyncWithnyToken(forceSync);
  const rplayOk = await checkAndSyncRplayToken(forceSync, autoNavigate);
  return { withny: withnyOk, rplay: rplayOk };
}

// 定時刷新 Rplay 頁面以刷新 Token
async function reloadRplayTabs() {
  try {
    const data = await chrome.storage.local.get(['auto_reload_rplay']);
    const isEnabled = (data.auto_reload_rplay !== undefined) ? data.auto_reload_rplay : true;
    if (!isEnabled) return;

    const tabs = await chrome.tabs.query({ url: '*://*.rplay.live/*' });
    if (tabs && tabs.length > 0) {
      for (const tab of tabs) {
        if (tab.id) {
          await chrome.tabs.reload(tab.id);
          console.log(`[StreamBot Extension] 🔄 已自動刷新 Rplay 頁面 (Tab ${tab.id}) 以取得最新 Token`);
        }
      }
    }
  } catch (e) {
    console.warn('[StreamBot Extension] 自動刷新 Rplay 失敗:', e);
  }
}

// 監聽來自 content_rplay.js 或 popup.js 的消息
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'rplay_token_found' && request.token) {
    const tok = request.token;
    if (tok !== lastRplayToken) {
      lastRplayToken = tok;
      sendToStreamBot('/update_token', { token: tok, platform: 'rplay' });
      console.log('[StreamBot Extension] 收到來自 ContentScript 之 Rplay Token 並成功同步');
    }
  } else if (request.action === 'force_sync') {
    const autoNav = (request.autoNavigate !== undefined) ? request.autoNavigate : true;
    doAutoSync(true, autoNav).then(res => sendResponse({ status: 'ok', result: res }));
    return true;
  } else if (request.action === 'set_auto_reload') {
    const isEnabled = !!request.enabled;
    chrome.storage.local.set({ auto_reload_rplay: isEnabled });
    if (isEnabled) {
      chrome.alarms.create('rplay_auto_reload_alarm', { periodInMinutes: 5 });
    } else {
      chrome.alarms.clear('rplay_auto_reload_alarm');
    }
    sendResponse({ status: 'ok', enabled: isEnabled });
    return true;
  } else if (request.action === 'open_rplay') {
    focusOrOpenUrl('*://*.rplay.live/*', 'https://rplay.live/').then(() => {
      sendResponse({ status: 'ok' });
    });
    return true;
  } else if (request.action === 'open_withny') {
    focusOrOpenUrl('*://*.withny.fun/*', 'https://withny.fun/').then(() => {
      sendResponse({ status: 'ok' });
    });
    return true;
  } else if (request.action === 'get_status') {
    sendResponse({
      withnyToken: lastWithnyToken,
      rplayToken: lastRplayToken
    });
  }
});

// 監聽 Cookie 變動 event (即時觸發)
chrome.cookies.onChanged.addListener((changeInfo) => {
  const domain = changeInfo.cookie.domain || '';
  if (domain.includes('withny.fun') || domain.includes('rplay.live')) {
    doAutoSync();
  }
});

// 建立 1 分鐘定時 Token 輪詢鬧鐘
chrome.alarms.create('streambot_token_poll', { periodInMinutes: 1 });

// 初始化並建立 5 分鐘 Rplay 自動刷新鬧鐘
chrome.storage.local.get(['auto_reload_rplay'], (data) => {
  const isEnabled = (data.auto_reload_rplay !== undefined) ? data.auto_reload_rplay : true;
  if (isEnabled) {
    chrome.alarms.create('rplay_auto_reload_alarm', { periodInMinutes: 5 });
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'streambot_token_poll') {
    doAutoSync();
  } else if (alarm.name === 'rplay_auto_reload_alarm') {
    reloadRplayTabs();
  }
});

// Extension 啟動時立即執行一次
doAutoSync();
