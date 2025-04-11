const express = require('express');
const cors = require('cors');
const logsRouter = require('./routes/logs');

const app = express();
const port = 3000;

app.use(cors());
app.use(express.json());
app.use('/logs', logsRouter);

app.listen(port, () => {
    console.log(`Serveur démarré sur http://localhost:${port}`);
});