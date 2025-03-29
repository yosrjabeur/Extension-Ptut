const { Pool } = require("pg"); 

// Configuration de la connexion à PostgreSQL
const pool = new Pool({  
    user: "postgres",
    host: "localhost",
    database: "tracker_db",
    password: "admin", 
    port: 5432,
});

// Vérification de la connexion
pool.connect()
    .then(() => console.log(" Connexion à PostgreSQL réussie"))
    .catch((err) => console.error(" Erreur de connexion à PostgreSQL :", err));

module.exports = pool; // Export du pool de connexions
