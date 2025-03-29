const express = require("express");
const pool = require("./db"); // Connexion à PostgreSQL
const router = express.Router();

// 📊 Nombre d'utilisateurs actifs quotidiens
router.get("/daily-active-users", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT COUNT(DISTINCT user_id) AS daily_active_users
            FROM logs
            WHERE timestamp >= NOW() - INTERVAL '7 days'
        `);
        res.json(result.rows[0]);
    } catch (error) {
        console.error("Erreur analyse (daily-active-users) :", error);
        res.status(500).json({ error: "Erreur lors de l'analyse" });
    }
});

// 📊 Nombre de sessions quotidiennes
router.get("/daily-sessions", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT COUNT(DISTINCT session_id) AS daily_sessions
            FROM logs
            WHERE timestamp >= NOW() - INTERVAL '7 days'
        `);
        res.json(result.rows[0]);
    } catch (error) {
        console.error("Erreur analyse (daily-sessions) :", error);
        res.status(500).json({ error: "Erreur lors de l'analyse" });
    }
});

// 📊 Pages vues quotidiennes
router.get("/daily-page-views", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT COUNT(*) AS daily_page_views
            FROM logs
            WHERE type = 'navigation' AND timestamp >= NOW() - INTERVAL '7 days'
        `);
        res.json(result.rows[0]);
    } catch (error) {
        console.error("Erreur analyse (daily-page-views) :", error);
        res.status(500).json({ error: "Erreur lors de l'analyse" });
    }
});

// 📊 Pages les plus visitées
router.get("/top-pages", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT url, COUNT(*) AS visit_count
            FROM logs
            WHERE type = 'navigation'
            GROUP BY url
            ORDER BY visit_count DESC
            LIMIT 10
        `);
        res.json(result.rows);
    } catch (error) {
        console.error("Erreur analyse (top-pages) :", error);
        res.status(500).json({ error: "Erreur lors de l'analyse" });
    }
});

// 📊 Fréquence de retour des utilisateurs
router.get("/returning-users", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT user_id, COUNT(DISTINCT DATE(timestamp)) AS visit_days
            FROM logs
            WHERE timestamp >= NOW() - INTERVAL '7 days'  -- ✅ Ajout du filtre sur 24h
            GROUP BY user_id
            ORDER BY visit_days DESC
        `);
        res.json(result.rows);
    } catch (error) {
        console.error("Erreur analyse (returning-users) :", error);
        res.status(500).json({ error: "Erreur lors de l'analyse" });
    }
});

// 📊 Utilisateurs rencontrant des problèmes
router.get("/users-with-errors", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT COUNT(DISTINCT user_id) AS affected_users
            FROM logs
            WHERE type = 'error' AND timestamp >= NOW() - INTERVAL '7 days' -- ✅ Ajout du filtre sur 24h
        `);
        res.json(result.rows[0]);
    } catch (error) {
        console.error("Erreur analyse (users-with-errors) :", error);
        res.status(500).json({ error: "Erreur lors de l'analyse" });
    }
});

// 📊 Problèmes les plus fréquents
router.get("/top-errors", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT details->>'message' AS error_message, COUNT(*) AS occurrences
            FROM logs
            WHERE type = 'error'
            GROUP BY error_message
            ORDER BY occurrences DESC
            LIMIT 5
        `);
        res.json(result.rows);
    } catch (error) {
        console.error("Erreur analyse (top-errors) :", error);
        res.status(500).json({ error: "Erreur lors de l'analyse" });
    }
});

module.exports = router;
