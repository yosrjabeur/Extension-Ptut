chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("Données envoyées à l'API :", message); // Ajoute un log pour vérifier les données

    fetch("http://localhost:3000/logs", { 
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(message)
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
        }
        return response.json();
    })
    .then(data => {
        console.log("Réponse du serveur :", data); // Log la réponse
        sendResponse({ status: "success", log: data });
    })
    .catch(error => {
        console.error("Erreur de fetch :", error);
        sendResponse({ status: "error", error: error.message });
    });

    return true; // Indique une réponse asynchrone
});
