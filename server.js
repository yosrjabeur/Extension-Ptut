const { body, validationResult } = require("express-validator");
const express = require("express");
const pool = require("./db");
const app = express();
const port = 3000;
const cors = require('cors');

app.use(express.json()); 
app.use(cors());

app.post("/logs", async (req, res) => {
    const { timestamp, type, details, url, user_agent, user_language, screen_resolution } = req.body;

    // Log les valeurs reçues
    console.log("Timestamp:", timestamp);
    console.log("Type:", type);
    console.log("Details:", details);
    console.log("URL:", url);
    console.log("User-Agent:", user_agent);
    console.log("User Language:", user_language);
    console.log("Screen Resolution:", screen_resolution);

    try {
        await pool.query(`
            INSERT INTO logs (timestamp, type, details, url, user_agent, user_language, screen_resolution)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        `, [timestamp, type, details, url, user_agent, user_language, screen_resolution]);

        res.json({ status: "success", message: "Log enregistré" });
    } catch (error) {
        console.error("Erreur d'enregistrement du log :", error);
        res.status(500).json({ status: "error", error: "Erreur serveur" });
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
