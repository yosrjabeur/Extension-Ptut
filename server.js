// server.js
import express from "express";
import pg from "pg";
import cors from "cors";

const app = express();
const port = 3000;

// Connexion à PostgreSQL
const pool = new pg.Pool({
  user: "postgres",
  host: "localhost",
  database: "tracker_db",
  password: "admin", 
  port: 5432,
});

app.use(cors());
app.use(express.json());

// Log toutes les requêtes reçues
app.use((req, res, next) => {
    console.log(`Requête reçue: ${req.method} ${req.url}`);
    next();
});

// Endpoint pour sauvegarder un log
app.post("/logs", async (req, res) => {
    console.log("Données reçues:", req.body);

    const { timestamp, type, details, url, userAgent, userLanguage, screenResolution } = req.body;

    if (!timestamp || !type || !details || !url || !userAgent || !userLanguage || !screenResolution) {
        return res.status(400).json({ error: "Champs manquants" });
    }

    try {
        const query = `
            INSERT INTO logs (timestamp, type, details, url, user_agent, user_language, screen_resolution) 
            VALUES ($1, $2, $3, $4, $5, $6, $7) 
            RETURNING *`;
        const values = [timestamp, type, JSON.stringify(details), url, userAgent, userLanguage, screenResolution];

        const result = await pool.query(query, values);
        console.log("Log enregistré dans PostgreSQL :", result.rows[0]);
        res.json({ message: "Log enregistré", log: result.rows[0] });
    } catch (error) {
        console.error("Erreur PostgreSQL :", error);
        res.status(500).json({ error: "Erreur lors de l'insertion du log" });
    }
});

// Endpoint pour récupérer les logs
app.get("/logs", async (req, res) => {
    try {
        const result = await pool.query("SELECT * FROM logs ORDER BY timestamp DESC");
        res.json(result.rows);
    } catch (error) {
        console.error("Erreur PostgreSQL :", error);
        res.status(500).json({ error: "Erreur lors de la récupération des logs" });
    }
});

app.listen(port, () => {
    console.log(`Serveur démarré sur http://localhost:${port}`);
});
