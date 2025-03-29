// popup.js
document.addEventListener("DOMContentLoaded", function () {
    let logsContainer = document.getElementById("logs");
    let clearButton = document.getElementById("clearLogs");

    // Charger les logs depuis le serveur
    function loadLogs() {
        fetch("http://localhost:3000/logs")
            .then(response => response.json())
            .then(logs => {
                if (logs.length === 0) {
                    logsContainer.innerHTML = "Aucune interaction enregistrée.";
                } else {
                    logsContainer.innerHTML = logs.map(log =>
                        `<p><strong>${log.type}</strong>: ${log.details?.text || log.details?.field || "N/A"} 
                        (${new Date(log.timestamp).toLocaleTimeString()})</p>`
                    ).join("");
                }
            })
            .catch(error => {
                console.error("Erreur de récupération des logs :", error);
                logsContainer.innerHTML = "Erreur de chargement des logs.";
            });
    }

    loadLogs(); // Charger les logs au démarrage

    // Ajouter un bouton pour rafraîchir les logs
    clearButton.addEventListener("click", function () {
        logsContainer.innerHTML = "Chargement...";
        loadLogs();
    });
});
