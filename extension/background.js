const API_URL = 'http://localhost:3000';

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    fetch(`${API_URL}/logs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(message),
    })
        .then(response => {
            if (!response.ok) throw new Error(`Erreur HTTP: ${response.status}`);
            return response.json();
        })
        .then(data => {
            sendResponse({ status: 'success', data });
        })
        .catch(error => {
            console.error('Erreur:', error);
            sendResponse({ status: 'error', error: error.message });
        });

    return true;
});