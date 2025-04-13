// content.js

// Fonction utilitaire pour obtenir la résolution d'écran
function getScreenResolution() {
    try {
        return `${window.innerWidth || 'unknown'}x${window.innerHeight || 'unknown'}`;
    } catch (e) {
        console.error('Erreur lors de la récupération de la résolution :', e);
        return 'unknown';
    }
}

// Fonction pour nettoyer les données
function sanitizeData(value) {
    if (typeof value === 'string') {
        return value.trim().substring(0, 200);
    }
    return value;
}

// Fonction pour générer un UUID
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Gestion des identifiants utilisateur et de session
const userId = localStorage.getItem('user_id') || generateUUID();
localStorage.setItem('user_id', userId);

let sessionId = sessionStorage.getItem('session_id');
if (!sessionId) {
    sessionId = generateUUID();
    sessionStorage.setItem('session_id', sessionId);
}

// File d'attente pour les logs en cas d'échec
const logQueue = [];
let isSending = false;

// Fonction pour envoyer les logs avec retry et file d'attente
async function sendLog(eventType, details, taskId = null, alertId = null, retryCount = 0) {
    const log = {
        timestamp: new Date().toISOString(),
        type: eventType,
        details: Object.fromEntries(
            Object.entries(details).map(([key, value]) => [key, sanitizeData(value)])
        ),
        url: window.location.href,
        domain: window.location.hostname,
        user_agent: navigator.userAgent || 'unknown',
        user_language: navigator.language || navigator.languages?.[0] || 'unknown',
        screen_resolution: getScreenResolution(),
        user_id: userId,
        session_id: sessionId,
        task_id: taskId,
        alert_id: alertId,
        page_title: document.title || 'unknown' // Ajout du titre de la page pour plus de contexte
    };

    logQueue.push(log);
    processLogQueue();
}

async function processLogQueue() {
    if (isSending || logQueue.length === 0) return;
    isSending = true;

    while (logQueue.length > 0) {
        const log = logQueue[0];
        try {
            await sendLogToBackend(log);
            logQueue.shift();
        } catch (error) {
            console.error('Échec de l’envoi du log après 3 tentatives :', error);
            if (log.retryCount >= 3) {
                logQueue.shift(); // Abandonner après 3 tentatives
            } else {
                log.retryCount = (log.retryCount || 0) + 1;
                const delay = Math.pow(2, log.retryCount) * 1000;
                await new Promise(resolve => setTimeout(resolve, delay));
            }
        }
    }
    isSending = false;
}

function sendLogToBackend(log) {
    return new Promise((resolve, reject) => {
        if (!chrome.runtime || !chrome.runtime.sendMessage) {
            reject(new Error('chrome.runtime non disponible'));
            return;
        }

        chrome.runtime.sendMessage(log, (response) => {
            if (chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message));
            } else if (response?.status === 'success') {
                console.log(`Log ${log.type} envoyé avec succès :`, response);
                resolve(response);
            } else {
                reject(new Error('Réponse inattendue : ' + JSON.stringify(response)));
            }
        });
    });
}

// Gestion des tâches actives (plusieurs tâches possibles)
const activeTasks = new Map();

function startTask(taskName, details) {
    const taskId = generateUUID();
    activeTasks.set(taskId, {
        name: taskName,
        startTime: new Date(),
        pendingEnd: false
    });
    sendLog('task_start', { name: taskName, ...details }, taskId);
    return taskId;
}

function endTask(taskId, details) {
    if (!activeTasks.has(taskId)) return;
    const task = activeTasks.get(taskId);
    const duration = ((new Date() - task.startTime) / 1000).toFixed(2);
    sendLog('task_end', { name: task.name, duration: duration + 's', ...details }, taskId);
    activeTasks.delete(taskId);
}

// Début d'une tâche "page_load" au chargement de la page
let pageStartTime = performance.now();
document.addEventListener('DOMContentLoaded', () => {
    const taskId = startTask('page_load', { page: window.location.pathname });
    activeTasks.get(taskId).pendingEnd = true; // Marquer comme tâche à terminer plus tard
});

// Gestion des tâches de soumission de formulaire
document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', (event) => {
        const taskName = form.getAttribute('action') || form.id || 'form_submit';
        const taskId = startTask(taskName, { feature: 'form_submission' });

        // Ne pas envoyer task_end immédiatement, attendre un changement d'URL ou un délai
        activeTasks.get(taskId).pendingEnd = true;

        // Vérifier la validité du formulaire
        if (!form.checkValidity()) {
            event.preventDefault();
            const invalidFields = Array.from(form.elements)
                .filter(el => el.validity && !el.validity.valid)
                .map(el => ({ name: el.name || el.id || el.tagName, error: el.validationMessage }));
            sendLog('error', {
                message: 'Erreur de validation de formulaire',
                fields: invalidFields
            }, taskId);
        }
    });
});

// Détecter les changements d'URL pour terminer les tâches en attente
let lastURL = window.location.href;
setInterval(() => {
    if (window.location.href !== lastURL) {
        activeTasks.forEach((task, taskId) => {
            if (task.pendingEnd) {
                endTask(taskId, { is_task_completed: true, reason: 'Changement d’URL détecté' });
            }
        });
        lastURL = window.location.href;
    }
}, 500);

// Terminer les tâches lors de la navigation
window.addEventListener('beforeunload', () => {
    activeTasks.forEach((task, taskId) => {
        if (task.pendingEnd) {
            endTask(taskId, { is_task_completed: true, reason: 'Navigation hors page' });
        }
    });
    const timeSpent = ((performance.now() - pageStartTime) / 1000).toFixed(2);
    sendLog('page_engagement', {
        active_time: timeSpent + 's',
        max_scroll_depth: maxScrollDepth.toFixed(2) + '%'
    });
});

// Détection des clics avec classification des fonctionnalités
document.addEventListener('click', (event) => {
    const target = event.target;
    const targetText = (target.innerText || target.value || '').toLowerCase();
    const elementId = target.id || target.getAttribute('data-id') || 'N/A';
    const elementType = target.tagName.toLowerCase();

    const features = {
        'button|submit': 'action_trigger',
        'a': 'navigation',
        'search|rechercher': 'search',
        'submit|envoyer': 'form_submission',
        'login|connexion': 'authentication',
        'help|aide': 'help_access',
        'tutorial|tutoriel': 'tutorial_access',
        'cancel|annuler': 'cancel'
    };

    let feature = null;
    let eventType = 'click';
    for (const [pattern, f] of Object.entries(features)) {
        const keywords = pattern.split('|');
        if (keywords.includes(elementType) || keywords.some(k => targetText.includes(k))) {
            feature = f;
            eventType = f === 'cancel' || f === 'help_access' || f === 'tutorial_access' ? f : 'click';
            break;
        }
    }

    const details = {
        element: elementType,
        text: target.innerText || target.value || '',
        element_id: elementId,
        position: { x: event.clientX, y: event.clientY },
        feature: feature || 'unknown'
    };

    const taskId = Array.from(activeTasks.keys())[0] || null;
    sendLog(eventType, details, taskId);
});

// Gestion des entrées utilisateur (inputs)
document.addEventListener('input', (event) => {
    const target = event.target;
    const field = target.name || target.id || target.tagName;
    const fieldType = target.type || 'text';

    const feature = fieldType === 'search' ? 'search' :
                   (fieldType === 'email' || fieldType === 'password') ? 'authentication' :
                   'form_input';

    const details = {
        field: field,
        field_type: fieldType,
        value: target.value,
        feature: feature
    };
    const taskId = Array.from(activeTasks.keys())[0] || null;
    sendLog('input', details, taskId);
});

// Gestion des erreurs JavaScript
window.addEventListener('error', (event) => {
    const taskId = Array.from(activeTasks.keys())[0] || null;
    sendLog('error', {
        message: event.message,
        file: event.filename,
        line: event.lineno,
        column: event.colno,
        stack: event.error?.stack || 'N/A'
    }, taskId);
});

// Détection des messages d'erreur ou d'alerte
const errorObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
        if (mutation.addedNodes.length) {
            const errorNodes = Array.from(mutation.addedNodes).filter(node =>
                node.nodeType === 1 && (
                    node.classList.contains('error') ||
                    node.classList.contains('alert') ||
                    node.classList.contains('warning') ||
                    node.textContent.toLowerCase().includes('error') ||
                    node.textContent.toLowerCase().includes('warning') ||
                    node.textContent.toLowerCase().includes('fail')
                )
            );
            errorNodes.forEach(node => {
                const taskId = Array.from(activeTasks.keys())[0] || null;
                sendLog('error', { message: node.textContent.trim() }, taskId);
            });
        }
    });
});
errorObserver.observe(document.body, { childList: true, subtree: true });

// Suivi des requêtes réseau
function trackNetworkRequests() {
    if (window.__fetchTracked) return;
    window.__fetchTracked = true;

    const originalFetch = window.fetch;
    window.fetch = async (...args) => {
        const url = args[0]?.toString() || 'unknown';
        const startTime = performance.now();
        try {
            const response = await originalFetch(...args);
            const duration = (performance.now() - startTime).toFixed(2);
            const taskId = Array.from(activeTasks.keys())[0] || null;
            sendLog(response.ok ? 'network' : 'network-error', {
                url: url,
                method: args[1]?.method || 'GET',
                status: response.status,
                duration: duration + 'ms',
                headers: Object.fromEntries(response.headers.entries())
            }, taskId);
            return response;
        } catch (error) {
            const taskId = Array.from(activeTasks.keys())[0] || null;
            sendLog('network-error', {
                url: url,
                method: args[1]?.method || 'GET',
                error: error.message
            }, taskId);
            throw error;
        }
    };

    const originalXHROpen = XMLHttpRequest.prototype.open;
    const originalXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...args) {
        this._method = method;
        this._url = url;
        return originalXHROpen.apply(this, [method, url, ...args]);
    };
    XMLHttpRequest.prototype.send = function(...args) {
        const startTime = performance.now();
        this.addEventListener('loadend', () => {
            const duration = (performance.now() - startTime).toFixed(2);
            const headers = {};
            this.getAllResponseHeaders().split('\r\n').forEach(header => {
                const [key, value] = header.split(': ');
                if (key && value) headers[key] = value;
            });
            const taskId = Array.from(activeTasks.keys())[0] || null;
            sendLog(this.status >= 400 ? 'network-error' : 'network', {
                url: this._url || 'unknown',
                method: this._method || 'GET',
                status: this.status,
                duration: duration + 'ms',
                headers: headers
            }, taskId);
        });
        return originalXHRSend.apply(this, args);
    };
}
trackNetworkRequests();

// Gestion des raccourcis clavier
document.addEventListener('keydown', (event) => {
    if (event.ctrlKey || event.altKey || event.metaKey) {
        const combination = `${event.ctrlKey ? 'Ctrl+' : ''}${event.altKey ? 'Alt+' : ''}${event.metaKey ? 'Meta+' : ''}${event.key}`;
        const taskId = Array.from(activeTasks.keys())[0] || null;
        sendLog('shortcut', { key: event.key, combination }, taskId);
    }
});

// Gestion des alertes/notifications
const alertTimes = new Map();
const alertObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
        if (mutation.addedNodes.length) {
            const alertNodes = Array.from(mutation.addedNodes).filter(node =>
                node.nodeType === 1 && (
                    node.classList.contains('alert') ||
                    node.classList.contains('notification') ||
                    node.classList.contains('modal') ||
                    node.textContent.toLowerCase().includes('error') ||
                    node.textContent.toLowerCase().includes('success') ||
                    node.textContent.toLowerCase().includes('warning')
                )
            );
            alertNodes.forEach(node => {
                const alertId = generateUUID();
                alertTimes.set(alertId, performance.now());
                sendLog('alert_shown', { message: node.textContent.trim() }, null, alertId);
            });
        }
    });
});
alertObserver.observe(document.body, { childList: true, subtree: true });

// Détection de la fermeture des alertes
document.addEventListener('click', (event) => {
    const target = event.target;
    if (target.closest('.alert, .notification, .modal') && (
        target.classList.contains('close') ||
        target.classList.contains('btn-close') ||
        target.textContent.toLowerCase().includes('close') ||
        target.textContent.toLowerCase().includes('dismiss')
    )) {
        const alertId = Array.from(alertTimes.keys())[0];
        if (alertId && alertTimes.has(alertId)) {
            const startTime = alertTimes.get(alertId);
            const responseTime = ((performance.now() - startTime) / 1000).toFixed(2);
            sendLog('alert_response', { response_time: responseTime + 's' }, null, alertId);
            alertTimes.delete(alertId);
        }
    }
});

// Suivi du temps passé par page et interactions
let lastInteractionTime = performance.now();
let maxScrollDepth = 0;

document.addEventListener('mousemove', () => lastInteractionTime = performance.now());
document.addEventListener('keydown', () => lastInteractionTime = performance.now());
document.addEventListener('click', () => lastInteractionTime = performance.now());

window.addEventListener('scroll', () => {
    const scrollDepth = window.scrollY + window.innerHeight;
    const docHeight = document.documentElement.scrollHeight;
    const scrollPercentage = (scrollDepth / docHeight) * 100;
    maxScrollDepth = Math.max(maxScrollDepth, scrollPercentage);
});