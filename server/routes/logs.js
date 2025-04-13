const express = require('express');
const pool = require('../db');
const redis = require('redis');

const router = express.Router();

// Connexion à Redis avec gestion d'erreur
let redisClient;
async function connectToRedis() {
    try {
        redisClient = redis.createClient({
            url: 'redis://172.22.198.52:6379',  
            socket: {
                connectTimeout: 5000,
                reconnectStrategy: (retries) => {
                    if (retries > 5) {
                        console.error('Impossible de se connecter à Redis après 5 tentatives');
                        return new Error('Redis connection failed');
                    }
                    return Math.min(retries * 1000, 5000);
                }
            }
        });

        redisClient.on('error', (err) => {
            console.error('Erreur Redis:', err);
        });

        await redisClient.connect();
        console.log('Connexion à Redis réussie');
        return true;
    } catch (err) {
        console.error('Erreur lors de la connexion à Redis:', err);
        return false;
    }
}

// Initialisation de Redis
(async () => {
    const redisConnected = await connectToRedis();
    if (!redisConnected) {
        console.warn('Redis non connecté. Les logs ne seront pas publiés en temps réel.');
    }
})();

// Route POST
router.post('/', async (req, res) => {
    const { timestamp, type, details, url, user_agent, user_language, screen_resolution, user_id, session_id, task_id, alert_id } = req.body;

    // Validation des champs requis
    if (!timestamp || !type || !user_id || !session_id) {
        return res.status(400).json({ error: 'Champs requis manquants (timestamp, type, user_id, session_id)' });
    }

    try {
        // Stocker dans PostgreSQL
        await pool.query(
            `INSERT INTO logs (timestamp, type, details, url, user_agent, user_language, screen_resolution, user_id, session_id, task_id, alert_id)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)`,
            [timestamp, type, details || {}, url, user_agent, user_language, screen_resolution, user_id, session_id, task_id, alert_id]
        );

        // Publier sur Redis si connecté
        if (redisClient?.isOpen) {
            await redisClient.publish('logs:realtime', JSON.stringify(req.body));
        } else {
            console.warn('Redis non connecté, log non publié:', req.body);
        }

        res.json({ status: 'success', message: 'Log enregistré' });
    } catch (error) {
        console.error('Erreur:', error);
        res.status(500).json({ error: 'Erreur serveur' });
    }
});

// Route GET (avec pagination)
router.get('/', async (req, res) => {
    const limit = parseInt(req.query.limit) || 50;
    const offset = parseInt(req.query.offset) || 0;

    try {
        const result = await pool.query(
            'SELECT * FROM logs ORDER BY timestamp DESC LIMIT $1 OFFSET $2',
            [limit, offset]
        );
        const total = (await pool.query('SELECT COUNT(*) FROM logs')).rows[0].count;
        res.json({ logs: result.rows, total });
    } catch (error) {
        console.error('Erreur:', error);
        res.status(500).json({ error: 'Erreur lors de la récupération des logs' });
    }
});

// Route DELETE
router.delete('/', async (req, res) => {
    try {
        await pool.query('DELETE FROM logs');
        res.json({ status: 'success', message: 'Logs effacés' });
    } catch (error) {
        console.error('Erreur:', error);
        res.status(500).json({ error: 'Erreur lors de l’effacement' });
    }
});

module.exports = router;