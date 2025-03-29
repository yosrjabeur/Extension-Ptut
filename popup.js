document.addEventListener("DOMContentLoaded", function () {
    let logsContainer = document.getElementById("logs");
    let clearButton = document.getElementById("clearLogs");

    // Charger les logs depuis chrome.storage.local
    chrome.storage.local.get(["logs"], (result) => {
        let logs = result.logs || [];
        if (logs.length === 0) {
            logsContainer.innerHTML = "Aucune interaction enregistrée.";
        } else {
            logsContainer.innerHTML = logs.map(log => 
                `<p><strong>${log.type}</strong>: ${log.details?.text || log.details?.key || "N/A"} (${new Date(log.timestamp).toLocaleTimeString()})</p>`
            ).join("");
        }
    });

    // Effacer les logs
    clearButton.addEventListener("click", function () {
        chrome.storage.local.set({ logs: [] }, () => {
            logsContainer.innerHTML = "Logs effacés.";
        });
    });
});
