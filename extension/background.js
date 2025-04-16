const API_URL = 'http://localhost:3000';

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("Message reçu dans background.js :", message);

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
            console.log("Réponse du serveur :", data);
            sendResponse({ status: 'success', data });
        })
        .catch(error => {
            console.error('Erreur lors de l\'envoi au serveur :', error);
            sendResponse({ status: 'error', error: error.message });
        });

    return true;
});