// Obtenir la résolution écran
function getScreenResolution() {
    try {
        const width = screen.width || window.innerWidth || 0;
        const height = screen.height || window.innerHeight || 0;
        return `${width}x${height}`;
    } catch (e) {
        console.error("Erreur lors de la récupération de la résolution écran :", e);
        return "unknown";
    }
}

// Fonction pour nettoyer les données
function sanitizeData(value) {
    try {
        if (value == null) return null;
        if (typeof value === 'string') {
            return value.trim().substring(0, 200);
        } else if (typeof value === 'object') {
            // Gérer les objets et tableaux récursivement
            return Object.fromEntries(
                Object.entries(value).map(([key, val]) => [key, sanitizeData(val)])
            );
        }
        return value;
    } catch (e) {
        console.error("Erreur lors de la sanitisation des données :", e);
        return String(value).substring(0, 200);
    }
}

// Fonction pour générer un UUID (identifiant unique)
function generateUUID() {
    try {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    } catch (e) {
        console.error("Erreur lors de la génération de l'UUID :", e);
        return Date.now().toString();
    }
}

// Historique des actions pour détecter les redondances
const actionHistory = new Map();

function checkRedundantAction(eventType, details, taskId) {
    try {
        // Créer une clé plus spécifique pour identifier les actions similaires
        const key = `${eventType}:${details.element}:${details.element_id || 'N/A'}:${details.text || 'N/A'}:${taskId || 'N/A'}`;
        const currentTime = Date.now();
        console.log(`[Redundant Check] Key: ${key}, Event Type: ${eventType}, Time: ${currentTime}`);

        if (actionHistory.has(key)) {
            const lastAction = actionHistory.get(key);
            const timeDiff = (currentTime - lastAction.timestamp) / 1000;
            console.log(`[Redundant Check] Found previous action. Time Diff: ${timeDiff}s, Count: ${lastAction.count}`);

            if (timeDiff <= 10) { // Fenêtre temporelle augmentée à 10 secondes
                lastAction.count += 1;
                console.log(`[Redundant Action] Detected! Count: ${lastAction.count}`);
                sendLog("redundant", {
                    action_type: eventType,
                    action_detail: key,
                    count: lastAction.count,
                    feature: details.feature || 'N/A'
                }, taskId);
            } else {
                console.log(`[Redundant Check] Time diff too large, resetting count.`);
                lastAction.count = 1;
            }
            lastAction.timestamp = currentTime;
        } else {
            console.log(`[Redundant Check] New action, initializing history.`);
            actionHistory.set(key, { timestamp: currentTime, count: 1 });
        }
    } catch (e) {
        console.error("Erreur lors de la détection d'action redondante :", e);
    }
}


// Récupérer ou générer un user_id (persistant pour chaque utilisateur)
let userId;
try {
    userId = localStorage.getItem('user_id');
    if (!userId) {
        userId = generateUUID();
        localStorage.setItem('user_id', userId);
    }
} catch (e) {
    console.error("Erreur lors de la gestion de user_id dans localStorage :", e);
    userId = generateUUID();
}

// Générer un session_id (unique pour chaque session)
let sessionId;
try {
    sessionId = sessionStorage.getItem('session_id');
    if (!sessionId) {
        sessionId = generateUUID();
        sessionStorage.setItem('session_id', sessionId);
    }
} catch (e) {
    console.error("Erreur lors de la gestion de session_id dans sessionStorage :", e);
    sessionId = generateUUID();
}

// Fonction pour envoyer les logs avec retry
function sendLog(eventType, details, taskId = null, alertId = null, retryCount = 0, useBeacon = false) {
    try {
        if (!chrome.runtime || !chrome.runtime.sendMessage) {
            console.error("chrome.runtime non disponible ! Détails :", {
                chromeRuntimeExists: !!chrome.runtime,
                sendMessageExists: !!(chrome.runtime && chrome.runtime.sendMessage),
                context: "Vérifiez que ce script s'exécute dans une extension Chrome correctement configurée."
            });
            return;
        }

        const log = {
            timestamp: new Date().toISOString(),
            type: eventType,
            details: Object.fromEntries(
                Object.entries(details || {}).map(([key, value]) => [key, sanitizeData(value)])
            ),
            url: window.location.href,
            domain: window.location.hostname || "unknown",
            user_agent: navigator.userAgent || "unknown",
            user_language: navigator.language || (navigator.languages && navigator.languages[0]) || "unknown",
            screen_resolution: getScreenResolution(),
            user_id: userId,
            session_id: sessionId,
            task_id: taskId,
            alert_id: alertId
        };

        if (useBeacon && navigator.sendBeacon) {
            // Utiliser sendBeacon pour les logs critiques (par ex. dans beforeunload)
            const blob = new Blob([JSON.stringify(log)], { type: 'application/json' });
            navigator.sendBeacon('http://localhost:3000/log', blob);
            console.log("Log envoyé via sendBeacon :", log);
            return;
        }

        chrome.runtime.sendMessage(log, (response) => {
            if (chrome.runtime.lastError) {
                console.error("Erreur d'envoi :", chrome.runtime.lastError.message);
                if (retryCount < 3) {
                    const delay = Math.pow(2, retryCount) * 1000;
                    setTimeout(() => sendLog(eventType, details, taskId, alertId, retryCount + 1), delay);
                } else {
                    console.error("Échec définitif de l'envoi du log après 3 tentatives.");
                }
            } else if (response?.status === "success") {
                console.log("Log enregistré :", response);
            } else {
                console.warn("Réponse inattendue :", response || "Aucune réponse");
            }
        });
    } catch (e) {
        console.error("Erreur lors de l'envoi du log :", e);
    }
}

// Fonction de debounce pour limiter les événements répétés
function debounce(fn, delay) {
    let timeoutId;
    return (...args) => {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => fn(...args), delay);
    };
}


// ----------- Captures d'événements -----------

// Gestion générique des tâches
let currentTaskId = null;
let pageStartTime;

try {
    pageStartTime = performance.now();
} catch (e) {
    console.error("Erreur lors de l'initialisation de pageStartTime :", e);
    pageStartTime = Date.now();
}

document.addEventListener('submit', (e) => {
    const form = e.target;
    const formId = form.id || 'unknown';
    const formTaskId = form.dataset.formTaskId || generateUUID(); // Récupérer ou générer un task_id

    const log = {
        type: 'task_end',
        timestamp: new Date().toISOString(),
        user_id: userId,  
        session_id: sessionId,  
        task_id: formTaskId,  
        domain: window.location.hostname,
        details: {
            name: `Form Submission - ${formId}`,
            is_task_completed: true,
            form_id: formId
        }
    };
    console.log('Log task_end:', log);
    sendLog(log.type, log.details, log.task_id);  // Utiliser sendLog correctement
});

// Début d'une tâche générique (chargement de page)
document.addEventListener('DOMContentLoaded', () => {
    try {
        currentTaskId = generateUUID();
        sendLog("task_start", { name: "page_load", page: window.location.pathname }, currentTaskId);
    } catch (e) {
        console.error("Erreur lors de l'événement DOMContentLoaded :", e);
    }
});



// Fin de la tâche page_load lors de la navigation
window.addEventListener("beforeunload", () => {
    try {
        if (currentTaskId) {
            const timeSpent = ((performance.now() - pageStartTime) / 1000).toFixed(2);
            sendLog("task_end", { name: "page_load", is_task_completed: true, duration: timeSpent + "s" }, currentTaskId, null, 0, true);
        }
    } catch (e) {
        console.error("Erreur lors de l'événement beforeunload (page_load) :", e);
    }
});

// Clics (avec détection des annulations et des fonctionnalités)
document.addEventListener("click", (event) => {
    try {
        const target = event.target;
        const targetText = ((target.innerText || target.value || '') + '').toLowerCase().trim();
        const elementId = target.id || target.getAttribute('data-id') || 'N/A';
        const elementType = target.tagName ? target.tagName.toLowerCase() : 'unknown';

        const isInteractiveElement = elementType === 'button' || elementType === 'a' || target.type === 'submit';

        let feature = null;
        if (elementType === 'button' || target.type === 'submit') {
            feature = 'action_trigger';
        } else if (elementType === 'a') {
            feature = 'navigation';
        } else if (targetText.includes('search') || targetText.includes('rechercher')) {
            feature = 'search';
        } else if (targetText.includes('submit') || targetText.includes('envoyer')) {
            feature = 'form_submission';
        } else if (targetText.includes('login') || targetText.includes('connexion')) {
            feature = 'authentication';
        }

        const details = {
            element: elementType,
            text: target.innerText || target.value || '',
            element_id: elementId,
            position: { x: event.clientX || 0, y: event.clientY || 0 },
            feature: feature
        };

        const isInForm = !!target.closest('form');
        const isExactCancel = targetText === 'cancel' || targetText === 'annuler';

        // Vérifier les actions redondantes
        checkRedundantAction("click", details, currentTaskId);

        if (isInteractiveElement && isInForm && isExactCancel) {
            sendLog("cancel", details, currentTaskId);
        } else if (targetText.includes('help') || targetText.includes('aide')) {
            sendLog("help_access", details, currentTaskId);
        } else if (targetText.includes('tutorial') || targetText.includes('tutoriel')) {
            sendLog("tutorial_access", details, currentTaskId);
        } else {
            sendLog("click", details, currentTaskId);
        }
    } catch (e) {
        console.error("Erreur lors de l'événement click :", e);
    }
});

// Inputs (avec détection des actions redondantes)
document.addEventListener("input", (event) => {
    try {
        const target = event.target;
        const form = target.closest('form');
        const formId = form ? (form.id || form.getAttribute('action') || generateUUID()) : 'unknown_form';
        const field = target.name || target.id || target.tagName || 'unknown';
        const fieldType = target.type || 'text';

        let feature = null;
        if (fieldType === 'search') {
            feature = 'search';
        } else if (fieldType === 'email' || fieldType === 'password') {
            feature = 'authentication';
        } else {
            feature = 'form_input';
        }

        const details = {
            element: 'input',
            field: field,
            field_type: fieldType,
            value: target.value || '',
            feature: feature,
            form_id: formId
        };

        // Vérifier les actions redondantes
        checkRedundantAction("input", details, currentTaskId);

        sendLog("input", details, currentTaskId);
    } catch (e) {
        console.error("Erreur lors de l'événement input :", e);
    }
});

// Gestion des formulaires (soumission, erreurs, quitter sans soumission)
const submittedForms = new Set();
try {
    document.querySelectorAll('form').forEach(form => {
        const formTaskId = generateUUID();
        const formId = form.id || form.getAttribute('action') || generateUUID();
    
        form.addEventListener('focusin', () => {
            try {
                if (!form.dataset.taskStarted) {
                    const taskName = form.getAttribute('action') || form.id || 'form_submit';
                    sendLog("task_start", { name: taskName, feature: "form_submission", form_id: formId }, formTaskId);
                    form.dataset.taskStarted = true;
                    form.dataset.formTaskId = formTaskId;  // Stocker le task_id dans le dataset du formulaire
                }
            } catch (e) {
                console.error("Erreur lors de l'événement focusin (formulaire) :", e);
            }
        });
    
        form.addEventListener('submit', (event) => {
            try {
                const taskName = form.getAttribute('action') || form.id || 'form_submit';
                console.log("Validation du formulaire :", form.checkValidity());
    
                const emailInputs = form.querySelectorAll('input[type="email"]');
                let emailValidationErrors = [];
                emailInputs.forEach(emailInput => {
                    const emailValue = emailInput.value || '';
                    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
                    if (emailInput.required && !emailPattern.test(emailValue)) {
                        emailValidationErrors.push({
                            name: emailInput.name || emailInput.id || 'email_field',
                            error: 'Adresse email invalide'
                        });
                    }
                });
    
                if (!form.checkValidity() || emailValidationErrors.length > 0) {
                    event.preventDefault();
                    const invalidFields = Array.from(form.elements || [])
                        .filter(el => el.validity && !el.validity.valid)
                        .map(el => ({
                            name: el.name || el.id || el.tagName || 'unknown',
                            error: el.validationMessage || 'Erreur inconnue'
                        }));
    
                    const allErrors = [...invalidFields, ...emailValidationErrors];
                    console.log("Erreurs détectées dans le formulaire :", allErrors);
    
                    sendLog("error", {
                        message: 'Form validation error',
                        fields: allErrors,
                        url: window.location.href,
                        form_id: formId
                    }, formTaskId);
    
                    sendLog("task_end", {
                        name: taskName,
                        is_task_completed: false,
                        feature: "form_submission",
                        form_id: formId,
                        reason: "Validation failed"
                    }, formTaskId);
                } else {
                    submittedForms.add(formId);
                    // La soumission réelle est gérée par le navigateur et les erreurs réseau sont capturées par trackNetworkRequests
                }
            } catch (e) {
                console.error("Erreur lors de l'événement submit (formulaire) :", e);
                sendLog("error", {
                    message: `Erreur inattendue lors de la soumission : ${e.message}`,
                    url: window.location.href,
                    form_id: formId
                }, formTaskId);
    
                sendLog("task_end", {
                    name: taskName,
                    is_task_completed: false,
                    feature: "form_submission",
                    form_id: formId,
                    reason: "Unexpected error"
                }, formTaskId);
            }
        });
    });
} catch (e) {
    console.error("Erreur lors de l'initialisation des écouteurs de formulaires :", e);
}

// Détection de quitter la page sans soumission
window.addEventListener('beforeunload', (event) => {
    try {
        const forms = document.querySelectorAll('form');
        let formTaskId = null;
        let shouldLogCancel = false;
        let cancelDetails = null;

        forms.forEach(form => {
            const formId = form.id || form.getAttribute('action') || generateUUID();
            if (submittedForms.has(formId)) {
                return;
            }

            const inputs = form.querySelectorAll('input, textarea, select');
            let filledFieldsCount = 0;

            inputs.forEach(input => {
                if (input.value && input.value.trim() !== '') {
                    filledFieldsCount++;
                }
            });

            if (filledFieldsCount >= 2) {
                shouldLogCancel = true;
                formTaskId = formTaskId || currentTaskId;
                cancelDetails = {
                    message: 'Utilisateur a quitté la page sans soumettre le formulaire',
                    feature: 'form_exit',
                    filled_fields_count: filledFieldsCount,
                    form_id: formId
                };
            }
        });

        if (shouldLogCancel) {
            sendLog("cancel", cancelDetails, formTaskId, null, 0, true);
        }
    } catch (e) {
        console.error("Erreur lors de l'événement beforeunload (formulaire) :", e);
    }
});

// Erreurs JavaScript
window.addEventListener("error", (event) => {
    try {
        const errorMessage = event.message || "Erreur JavaScript inconnue";
        sendLog("error", {
            message: errorMessage,
            file: event.filename || "unknown",
            line: event.lineno || 0,
            column: event.colno || 0,
            stack: event.error?.stack || "N/A"
        }, currentTaskId);
    } catch (e) {
        console.error("Erreur lors de l'événement error (JavaScript) :", e);
    }
});

// Détecter les messages d'erreur affichés (popups, notifications, etc.)
const errorObserver = new MutationObserver(debounce((mutations) => {
    try {
        mutations.forEach((mutation) => {
            if (mutation.addedNodes.length) {
                const errorMessages = Array.from(mutation.addedNodes).filter(node =>
                    node.nodeType === 1 && (
                        node.classList?.contains('error') ||
                        node.classList?.contains('alert') ||
                        node.classList?.contains('warning') ||
                        (node.textContent && node.textContent.toLowerCase().includes('error')) ||
                        (node.textContent && node.textContent.toLowerCase().includes('warning')) ||
                        (node.textContent && node.textContent.toLowerCase().includes('fail'))
                    )
                );
                errorMessages.forEach(errorNode => {
                    const errorMessage = errorNode.textContent ? errorNode.textContent.trim() : "Message d'erreur inconnu";
                    sendLog("error", {
                        message: errorMessage,
                        url: window.location.href
                    }, currentTaskId);
                });
            }
        });
    } catch (e) {
        console.error("Erreur dans l'errorObserver :", e);
    }
}, 500));
try {
    errorObserver.observe(document.body, { childList: true, subtree: true });
} catch (e) {
    console.error("Erreur lors de l'initialisation de l'errorObserver :", e);
}

// Requêtes réseau (détection des erreurs réseau)
function trackNetworkRequests() {
    try {
        if (window.__fetchTracked) return;
        window.__fetchTracked = true;

        if (window.fetch) {
            const originalFetch = window.fetch;
            window.fetch = async function (...args) {
                const url = args[0]?.toString() || 'unknown';
                const startTime = performance.now();
                try {
                    const response = await originalFetch(...args);
                    const endTime = performance.now();
                    const logType = response.ok ? "network" : "network-error";
                    const details = {
                        url: url,
                        method: args[1]?.method || 'GET',
                        status: response.status || 0,
                        duration: (endTime - startTime).toFixed(2) + "ms",
                        headers: response.headers ? Object.fromEntries(response.headers.entries()) : {}
                    };
                    sendLog(logType, details, currentTaskId);
                    return response;
                } catch (error) {
                    sendLog("network-error", {
                        url: url,
                        method: args[1]?.method || 'GET',
                        error: error.message || "Erreur réseau inconnue"
                    }, currentTaskId);
                    throw error;
                }
            };
        }

        if (XMLHttpRequest) {
            const originalXHROpen = XMLHttpRequest.prototype.open;
            const originalXHRSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function (method, url, ...args) {
                try {
                    this._method = method;
                    this._url = url;
                    return originalXHROpen.apply(this, [method, url, ...args]);
                } catch (e) {
                    console.error("Erreur lors de l'appel à XMLHttpRequest.open :", e);
                    throw e;
                }
            };
            XMLHttpRequest.prototype.send = function (...args) {
                try {
                    const xhr = this;
                    const startTime = performance.now();
                    xhr.addEventListener("loadend", () => {
                        try {
                            const url = xhr._url || 'unknown';
                            const endTime = performance.now();
                            const headers = {};
                            const responseHeaders = xhr.getAllResponseHeaders();
                            if (responseHeaders) {
                                responseHeaders.split('\r\n').forEach(header => {
                                    const [key, value] = header.split(': ');
                                    if (key && value) headers[key] = value;
                                });
                            }
                            const logType = xhr.status >= 400 ? "network-error" : "network";
                            const details = {
                                url: url,
                                method: xhr._method || 'GET',
                                status: xhr.status || 0,
                                duration: (endTime - startTime).toFixed(2) + "ms",
                                headers: headers
                            };
                            sendLog(logType, details, currentTaskId);
                        } catch (e) {
                            console.error("Erreur dans l'événement loadend de XMLHttpRequest :", e);
                        }
                    });
                    return originalXHRSend.apply(xhr, args);
                } catch (e) {
                    console.error("Erreur lors de l'appel à XMLHttpRequest.send :", e);
                    throw e;
                }
            };
        }
    } catch (e) {
        console.error("Erreur lors de l'initialisation de trackNetworkRequests :", e);
    }
}
trackNetworkRequests();

// Raccourcis clavier
document.addEventListener("keydown", (event) => {
    try {
        if (event.ctrlKey || event.altKey || event.metaKey) {
            const combination = `${event.ctrlKey ? "Ctrl+" : ""}${event.altKey ? "Alt+" : ""}${event.metaKey ? "Meta+" : ""}${event.key || "unknown"}`;
            sendLog("shortcut", { key: event.key || "unknown", combination }, currentTaskId);
        }
    } catch (e) {
        console.error("Erreur lors de l'événement keydown :", e);
    }
});

// Gestion des alertes/notifications
const alertTimes = new Map();
const alertObserver = new MutationObserver(debounce((mutations) => {
    try {
        mutations.forEach((mutation) => {
            if (mutation.addedNodes.length) {
                const alertMessages = Array.from(mutation.addedNodes).filter(node =>
                    node.nodeType === 1 && (
                        node.classList?.contains('alert') ||
                        node.classList?.contains('notification') ||
                        node.classList?.contains('modal') ||
                        (node.textContent && node.textContent.toLowerCase().includes('error')) ||
                        (node.textContent && node.textContent.toLowerCase().includes('success')) ||
                        (node.textContent && node.textContent.toLowerCase().includes('warning'))
                    )
                );
                alertMessages.forEach(alertNode => {
                    const alertId = generateUUID();
                    alertTimes.set(alertId, performance.now());
                    sendLog("alert_shown", { message: alertNode.textContent ? alertNode.textContent.trim() : "Message inconnu" }, null, alertId);
                });
            }
        });
    } catch (e) {
        console.error("Erreur dans l'alertObserver :", e);
    }
}, 500));
try {
    alertObserver.observe(document.body, { childList: true, subtree: true });
} catch (e) {
    console.error("Erreur lors de l'initialisation de l'alertObserver :", e);
}

// Détecter la fermeture des alertes
document.addEventListener("click", (event) => {
    try {
        const target = event.target;
        const closestAlert = target.closest('.alert, .notification, .modal');
        if (closestAlert && (
            target.classList?.contains('close') ||
            target.classList?.contains('btn-close') ||
            (target.textContent && target.textContent.toLowerCase().includes('close')) ||
            (target.textContent && target.textContent.toLowerCase().includes('dismiss'))
        )) {
            const alertId = Array.from(alertTimes.keys())[0];
            if (alertId && alertTimes.has(alertId)) {
                const startTime = alertTimes.get(alertId);
                const responseTime = ((performance.now() - startTime) / 1000).toFixed(2);
                sendLog("alert_response", { response_time: responseTime + "s" }, null, alertId);
                alertTimes.delete(alertId);
            }
        }
    } catch (e) {
        console.error("Erreur lors de la détection de la fermeture d'une alerte :", e);
    }
});

// Suivi du temps passé par page
let lastInteractionTime;
try {
    lastInteractionTime = performance.now();
} catch (e) {
    console.error("Erreur lors de l'initialisation de lastInteractionTime :", e);
    lastInteractionTime = Date.now();
}

const updateLastInteraction = debounce(() => {
    try {
        lastInteractionTime = performance.now();
    } catch (e) {
        console.error("Erreur lors de la mise à jour de lastInteractionTime :", e);
        lastInteractionTime = Date.now();
    }
}, 500);

document.addEventListener("mousemove", updateLastInteraction);
document.addEventListener("keydown", updateLastInteraction);
document.addEventListener("click", updateLastInteraction);

// Suivi du scroll
let maxScrollDepth = 0;
const updateScrollDepth = debounce(() => {
    try {
        const scrollDepth = window.scrollY + (window.innerHeight || 0);
        const docHeight = document.documentElement.scrollHeight || 1;
        const scrollPercentage = (scrollDepth / docHeight) * 100;
        maxScrollDepth = Math.max(maxScrollDepth, scrollPercentage);
    } catch (e) {
        console.error("Erreur lors de l'événement scroll :", e);
    }
}, 500);
window.addEventListener("scroll", updateScrollDepth);

// Envoi du temps passé et du scroll depth lors de la navigation
window.addEventListener("beforeunload", () => {
    try {
        const activeTime = ((lastInteractionTime - pageStartTime) / 1000).toFixed(2);
        sendLog("page_engagement", {
            active_time: activeTime + "s",
            max_scroll_depth: maxScrollDepth.toFixed(2) + "%"
        }, null, null, 0, true);
    } catch (e) {
        console.error("Erreur lors de l'événement beforeunload (page_engagement) :", e);
    }
});