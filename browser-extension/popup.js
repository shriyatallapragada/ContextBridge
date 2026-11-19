const preview = document.getElementById('preview');
const status = document.getElementById('status');
const save = document.getElementById('save');
preview.addEventListener('input', () => { save.disabled = !preview.value.trim(); });
document.getElementById('capture').addEventListener('click', async () => {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id) throw new Error('No active tab.');
    const results = await chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: () => {
        const text = window.getSelection()?.toString() || '';
        return {text: text.slice(0, 100001), tooLarge: text.length > 100000};
      }
    });
    const result = results[0]?.result;
    if (result?.tooLarge) throw new Error('Select a smaller excerpt (100,000 characters maximum).');
    if (!result?.text.trim()) throw new Error('Select chat messages on the page first, then reopen this popup.');
    preview.value = result.text;
    save.disabled = false;
    status.textContent = 'Captured selected text. Review and edit before saving.';
  } catch (error) {
    status.textContent = error.message || 'Cannot read this page. Copy and paste the messages into ContextBridge instead.';
  }
});
save.addEventListener('click', async () => {
  try {
    if (!preview.value.trim()) return;
    await chrome.downloads.download({
      url: 'data:text/plain;charset=utf-8,' + encodeURIComponent(preview.value),
      filename: 'contextbridge-chat.txt', saveAs: true
    });
    status.textContent = 'Download requested. Import the file in ContextBridge → Chat bridge.';
  } catch (error) { status.textContent = error.message || 'Could not save the file.'; }
});
