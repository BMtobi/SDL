// Content script for Rplay to extract _AUTHORIZATION_ from localStorage
function checkAndSendRplayToken() {
  try {
    const token = localStorage.getItem('_AUTHORIZATION_') || localStorage.getItem('rplay_token');
    if (token) {
      const cleanToken = token.trim().replace(/^["']|["']$/g, '');
      if (cleanToken.startsWith('eyJ') && cleanToken.length > 50) {
        chrome.runtime.sendMessage({ action: 'rplay_token_found', token: cleanToken });
      }
    }
  } catch (e) {}
}

checkAndSendRplayToken();
setInterval(checkAndSendRplayToken, 2000);

window.addEventListener('storage', (e) => {
  if (e.key === '_AUTHORIZATION_' || e.key === 'rplay_token') {
    checkAndSendRplayToken();
  }
});
