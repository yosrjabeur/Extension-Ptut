const API_URL = 'http://localhost:3000';

document.addEventListener('DOMContentLoaded', () => {
    const logsContainer = document.getElementById('logs');
    const refreshButton = document.getElementById('refreshLogs');
    const clearButton = document.getElementById('clearLogs');

    const loadLogs = async () => {
        try {
            logsContainer.innerHTML = 'Chargement...';
            const response = await fetch(`${API_URL}/logs`);
            if (!response.ok) throw new Error('Erreur réseau');
            const logs = await response.json();

            logsContainer.innerHTML = logs.length === 0
                ? 'Aucune interaction enregistrée.'
                : logs.map(log => `
                    <p>
                        <strong>${log.type}</strong>: 
                        ${log.details?.text || log.details?.field || 'N/A'} 
                        (${new Date(log.timestamp).toLocaleString('fr-FR')})
                    </p>
                `).join('');
        } catch (error) {
            console.error('Erreur de chargement:', error);
            logsContainer.innerHTML = 'Erreur de chargement des logs.';
        }
    };

    const clearLogs = async () => {
        try {
            await fetch(`${API_URL}/logs`, { method: 'DELETE' });
            logsContainer.innerHTML = 'Logs effacés.';
        } catch (error) {
            console.error('Erreur lors de l’effacement:', error);
            logsContainer.innerHTML = 'Erreur lors de l’effacement.';
        }
    };

    refreshButton.addEventListener('click', loadLogs);
    clearButton.addEventListener('click', clearLogs);

    loadLogs();
});