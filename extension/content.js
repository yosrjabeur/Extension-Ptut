// Obtenir la résolution écran
function getScreenResolution() {
    if (typeof window !== "undefined" && window.innerWidth && window.innerHeight) {
        return `${window.innerWidth}x${window.innerHeight}`;
    }
    return "unknown"; 
}

// Gestion session/user ID
let sessionId = crypto.randomUUID(); 
let userId = localStorage.getItem("userId") || crypto.randomUUID();
localStorage.setItem("userId", userId);

// Fonction pour envoyer les logs
function sendLog(eventType, details, taskId = null) {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
        let log = {
            timestamp: new Date().toISOString(),
            type: eventType,
            details: details,
            url: window.location.href,
            user_agent: navigator.userAgent || "unknown",
            user_language: navigator.language || navigator.userLanguage,
            screen_resolution: getScreenResolution(),
            user_id: userId,
            session_id: sessionId,
            task_id: taskId
        };
        chrome.runtime.sendMessage(log, (response) => {
            if (chrome.runtime.lastError) {
                console.error("Erreur d'envoi :", chrome.runtime.lastError.message);
            } else if (response?.status === "success") {
                console.log("Log enregistré :", response.log);
            } else {
                console.warn("Réponse inattendue :", response);
            }
        });
    } else {
        console.error("chrome.runtime non disponible !");
    }
}

// ----------- Captures d'événements -----------

// Clics
document.addEventListener("click", (event) => {
    sendLog("click", {
        element: event.target.tagName,
        text: event.target.innerText,
        position: { x: event.clientX, y: event.clientY }
    });
});

// Inputs
document.addEventListener("input", (event) => {
    sendLog("input", {
        field: event.target.name || event.target.id || event.target.tagName,
        value: event.target.value
    });
});

// Erreurs JS
window.addEventListener("error", (event) => {
    sendLog("error", {
        message: event.message,
        file: event.filename,
        line: event.lineno,
        column: event.colno,
        stack: event.error?.stack || "N/A"
    });
});

// Network Requests
function trackNetworkRequests() {
    if (window.__fetchTracked) return;  // Empêcher double tracking
    window.__fetchTracked = true;

    // Fetch
    const originalFetch = window.fetch;
    window.fetch = async function (...args) {
        const startTime = performance.now();
        try {
            const response = await originalFetch(...args);
            const endTime = performance.now();
            sendLog("network", {
                url: args[0],
                status: response.status,
                duration: (endTime - startTime).toFixed(2) + "ms"
            });
            return response;
        } catch (error) {
            sendLog("network-error", { url: args[0], error: error.message });
            throw error;
        }
    };

    // XMLHttpRequest
    const originalXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (...args) {
        const xhr = this;
        const startTime = performance.now();
        xhr.addEventListener("loadend", () => {
            const endTime = performance.now();
            sendLog("network", {
                url: xhr.responseURL,
                status: xhr.status,
                duration: (endTime - startTime).toFixed(2) + "ms"
            });
        });
        return originalXHRSend.apply(xhr, args);
    };
}
trackNetworkRequests();

// Navigation & Temps sur page
let pageStartTime = performance.now();
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
        const timeSpent = ((performance.now() - pageStartTime) / 1000).toFixed(2);
        sendLog("navigation", { duration: timeSpent + "s" });
    }
});

// Captures raccourcis clavier
document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.altKey || event.metaKey) {
        let combination = `${event.ctrlKey ? "Ctrl+" : ""}${event.altKey ? "Alt+" : ""}${event.metaKey ? "Meta+" : ""}${event.key}`;
        sendLog("shortcut", { key: event.key, combination });
    }
});

// Initialisation DOM
document.addEventListener("DOMContentLoaded", () => {
    // Suivi de tâches
    let currentTaskId = null;
    document.querySelectorAll(".search-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            currentTaskId = crypto.randomUUID();
            sendLog("task_start", { name: "search" }, currentTaskId);
        });
    });

    document.addEventListener("task_complete_event", () => {
        if (currentTaskId) {
            sendLog("task_end", { name: "search", success: true }, currentTaskId);
            currentTaskId = null;
        }
    });

    // Aide
    document.querySelectorAll(".help-link, .tooltip").forEach(el => {
        el.addEventListener("click", () => {
            sendLog("help", { element: el.tagName, text: el.innerText });
        });
    });
});

// Gestion Alertes/Notifs
let alertStartTime = null;
document.addEventListener("alert_shown", () => {
    alertStartTime = performance.now();
});
document.addEventListener("alert_dismissed", () => {
    if (alertStartTime !== null) {
        const responseTime = ((performance.now() - alertStartTime) / 1000).toFixed(2);
        sendLog("alert_response", { response_time: responseTime + "s" });
        alertStartTime = null;
    }
});
