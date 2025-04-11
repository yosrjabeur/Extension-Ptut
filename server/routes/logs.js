const express = require('express');
const pool = require('../db');

const router = express.Router();

router.post('/', async (req, res) => {
    const { timestamp, type, details, url, user_agent, user_language, screen_resolution, user_id, session_id, task_id } = req.body;

    if (!timestamp || !type) {
        return res.status(400).json({ error: 'Timestamp et type requis' });
    }

    try {
        await pool.query(
            `INSERT INTO logs (timestamp, type, details, url, user_agent, user_language, screen_resolution, user_id, session_id, task_id)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
            [timestamp, type, details, url, user_agent, user_language, screen_resolution, user_id, session_id, task_id]
        );
        res.json({ status: 'success', message: 'Log enregistré' });
    } catch (error) {
        console.error('Erreur:', error);
        res.status(500).json({ error: 'Erreur serveur' });
    }
});

router.get('/', async (req, res) => {
    try {
        const result = await pool.query('SELECT * FROM logs ORDER BY timestamp DESC LIMIT 50');
        res.json(result.rows);
    } catch (error) {
        console.error('Erreur:', error);
        res.status(500).json({ error: 'Erreur lors de la récupération des logs' });
    }
});

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