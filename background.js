chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("📡 Message reçu :", message);

    if (!message) {
        sendResponse({ status: "error", error: "Message vide" });
        return false;
    }

    try {
        fetch("http://localhost:3000/logs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                timestamp: message.timestamp,
                type: message.type,
                details: message.details,
                url: message.url,
                userAgent: message.userAgent, // On ne récupère plus depuis window !
                userLanguage: message.userLanguage,
                screenResolution: message.screenResolution
            })
        })
        .then(response => response.json())
        .then(data => {
            console.log("Log enregistré :", data);
            sendResponse({ status: "success", log: data });
        })
        .catch(error => {
            console.error("Erreur d'envoi au serveur :", error);
            sendResponse({ status: "error", error: error.message });
        });

        return true; // Indique que sendResponse sera appelé de manière asynchrone
    } catch (error) {
        console.error("Erreur inattendue :", error);
        sendResponse({ status: "error", error: error.message });
        return false;
    }
});
