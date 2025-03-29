function getScreenResolution() {
    if (typeof window !== "undefined" && window.innerWidth && window.innerHeight) {
        return `${window.innerWidth}x${window.innerHeight}`;
    }
    return "unknown"; 
}

function sendLog(eventType, details) {
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      let log = {
          timestamp: new Date().toISOString(),
          type: eventType,
          details: details,
          url: window.location.href,
          userAgent: navigator.userAgent,
          userLanguage: navigator.language || navigator.userLanguage,
          screenResolution: getScreenResolution() // Utilisation de la fonction sécurisée
      };

      chrome.runtime.sendMessage(log, (response) => {
        if (chrome.runtime.lastError) {
            console.error("Erreur d'envoi du log :", chrome.runtime.lastError.message);
        } else if (response && response.status === "success") {
            console.log("Log enregistré :", response.log);
        } else {
            console.warn("Aucune réponse ou réponse inattendue :", response);
        }
    });    
  } else {
      console.error("❌ chrome.runtime non disponible !");
  }
}

 


// Capture les clics avec position et élément
document.addEventListener("click", (event) => {
  sendLog("click", {
      element: event.target.tagName,
      text: event.target.innerText,
      position: { x: event.clientX, y: event.clientY }
  });
});

// Capture les frappes clavier avec champ ciblé
document.addEventListener("input", (event) => {
  sendLog("input", {
      field: event.target.name || event.target.id || event.target.tagName,
      value: event.target.value
  });
});

// Capture les erreurs JavaScript
window.addEventListener("error", (event) => {
  sendLog("error", {
      message: event.message,
      file: event.filename,
      line: event.lineno,
      column: event.colno,
      stack: event.error?.stack || "N/A"
  });
});

// Capture les requêtes réseau (XHR et Fetch)
function trackNetworkRequests() {
  let originalFetch = window.fetch;
  window.fetch = async function (...args) {
      let startTime = performance.now();
      try {
          let response = await originalFetch(...args);
          let endTime = performance.now();
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

  let originalXHR = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
      let startTime = performance.now();
      this.addEventListener("loadend", () => {
          let endTime = performance.now();
          sendLog("network", {
              url: this.responseURL,
              status: this.status,
              duration: (endTime - startTime).toFixed(2) + "ms"
          });
      });
      return originalXHR.apply(this, args);
  };
}

trackNetworkRequests();

// Capture la navigation et le temps passé sur la page
let pageStartTime = performance.now();

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
        let timeSpent = ((performance.now() - pageStartTime) / 1000).toFixed(2);
        sendLog("navigation", { duration: timeSpent + "s" });
    }
});

