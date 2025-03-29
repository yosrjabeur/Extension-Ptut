// Import the functions you need from the SDKs you need
import { initializeApp } from "https://www.gstatic.com/firebasejs/9.10.0/firebase-app.js";
import { getFirestore, collection, addDoc } from "https://www.gstatic.com/firebasejs/9.10.0/firebase-firestore.js";

// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
// For Firebase JS SDK v7.20.0 and later, measurementId is optional
const firebaseConfig = {
  apiKey: "AIzaSyDgaKfP-qHLCAJjulsNIpL4_rwEpYtNnDE",
  authDomain: "sauvegarde-data.firebaseapp.com",
  projectId: "sauvegarde-data",
  storageBucket: "sauvegarde-data.firebasestorage.app",
  messagingSenderId: "637057178492",
  appId: "1:637057178492:web:a91382a8b2ba24aaef22f9",
  measurementId: "G-72SPTTPVB2"
};

// Initialiser Firebase et Firestore
const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

// 🎯 Fonction pour enregistrer les logs avancés
async function saveLog(log) {
    try {
        await addDoc(collection(db, "logs"), log);
        console.log("🔥 Log ajouté dans Firestore :", log);
    } catch (error) {
        console.error("❌ Erreur Firebase :", error);
    }
}

// 🔄 Écoute les messages envoyés par `content.js`
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("📡 Message reçu :", message);

    if (message) {
        saveLog(message);
        sendResponse({ status: "OK" });
    } else {
        sendResponse({ status: "ERROR", error: "Message vide" });
    }

    return true; // Permet les réponses asynchrones
});
