document.addEventListener('DOMContentLoaded', async () => {
  const serverStatus = document.getElementById('serverStatus');
  const withnyStatus = document.getElementById('withnyStatus');
  const rplayStatus = document.getElementById('rplayStatus');
  const btnSync = document.getElementById('btnSync');
  const toastMsg = document.getElementById('toastMsg');
  const rowRplay = document.getElementById('rowRplay');
  const rowWithny = document.getElementById('rowWithny');
  const toggleAutoReload = document.getElementById('toggleAutoReload');

  // 讀取並初始化自動刷新開關狀態 (預設開啟 true)
  chrome.storage.local.get(['auto_reload_rplay'], (data) => {
    if (toggleAutoReload) {
      toggleAutoReload.checked = (data.auto_reload_rplay !== undefined) ? data.auto_reload_rplay : true;
    }
  });

  if (toggleAutoReload) {
    toggleAutoReload.addEventListener('change', () => {
      const isEnabled = toggleAutoReload.checked;
      chrome.storage.local.set({ auto_reload_rplay: isEnabled });
      chrome.runtime.sendMessage({ action: 'set_auto_reload', enabled: isEnabled });
    });
  }

  async function updateUI() {
    try {
      const res = await fetch('http://127.0.0.1:18730/api/status', { cache: 'no-cache' });
      if (res.ok) {
        serverStatus.textContent = '🟢 在線';
        serverStatus.className = 'badge online';
      } else {
        throw new Error();
      }
    } catch (e) {
      serverStatus.textContent = '🔴 未連線';
      serverStatus.className = 'badge offline';
    }

    chrome.runtime.sendMessage({ action: 'get_status' }, (response) => {
      if (response) {
        if (response.withnyToken) {
          withnyStatus.textContent = '🟢 已同步';
          withnyStatus.className = 'badge online';
        } else {
          withnyStatus.textContent = '⚪ 等待中';
          withnyStatus.className = 'badge offline';
        }

        if (response.rplayToken) {
          rplayStatus.textContent = '🟢 已同步';
          rplayStatus.className = 'badge online';
        } else {
          rplayStatus.textContent = '⚪ 等待中';
          rplayStatus.className = 'badge offline';
        }
      }
    });
  }

  btnSync.addEventListener('click', () => {
    btnSync.disabled = true;
    btnSync.textContent = '⏳ 跳轉與同步中...';
    
    // 發送 force_sync 並開啟/切換至 Rplay 頁面
    chrome.runtime.sendMessage({ action: 'force_sync', autoNavigate: true }, (response) => {
      setTimeout(() => {
        btnSync.disabled = false;
        btnSync.textContent = '⚡ 更新並自動轉到 Rplay 頁面';
        toastMsg.style.display = 'block';
        setTimeout(() => toastMsg.style.display = 'none', 3000);
        updateUI();
      }, 700);
    });
  });

  // 點擊 Rplay 狀態行直接轉到 Rplay
  if (rowRplay) {
    rowRplay.addEventListener('click', () => {
      chrome.runtime.sendMessage({ action: 'open_rplay' });
    });
  }

  // 點擊 Withny 狀態行直接轉到 Withny
  if (rowWithny) {
    rowWithny.addEventListener('click', () => {
      chrome.runtime.sendMessage({ action: 'open_withny' });
    });
  }

  updateUI();
});
