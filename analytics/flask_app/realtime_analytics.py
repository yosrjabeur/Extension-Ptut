import redis
import json
import pandas as pd
from flask import Flask, jsonify
import threading
import queue
from datetime import datetime

# Connexion à Redis
r = redis.Redis(host="localhost", port=6379, decode_responses=True)
pubsub = r.pubsub()
pubsub.subscribe("logs:realtime")

# File pour stocker les logs
log_queue = queue.Queue()

# Structures pour suivre les métriques
task_times = []  # Temps d'exécution des tâches
errors = 0       # Compteur d'erreurs
cancels = 0      # Compteur d'annulations
total_actions = 0  # Total des actions
feature_usage = {}  # Fréquence des fonctionnalités
clicks_per_task = {}  # Clics par tâche
help_usage = 0    # Utilisation de l'aide
tasks_started = 0  # Tâches commencées
tasks_completed = 0  # Tâches terminées
shortcuts = 0     # Utilisation des raccourcis
alert_responses = []  # Temps de réponse aux alertes

# Constantes pour comparaison
expert_avg = {"search": 5, "export": 10}  # Temps moyen expert (secondes)
optimal_clicks = {"search": 2, "export": 3}  # Clics optimaux par tâche
key_features = ["search", "export"]  # Fonctionnalités clés

# Traitement des logs
def process_log(log):
    global errors, cancels, total_actions, help_usage, tasks_started, tasks_completed, shortcuts

    total_actions += 1
    log_type = log["type"]
    details = log["details"] or {}

    # Temps d'exécution des tâches
    if log_type == "task_start":
        tasks_started += 1
        task_times.append({
            "task_id": log["task_id"],
            "start": pd.to_datetime(log["timestamp"]),
            "name": details.get("name")
        })
    elif log_type == "task_end" and log["task_id"]:
        for task in task_times:
            if task["task_id"] == log["task_id"] and "end" not in task:
                task["end"] = pd.to_datetime(log["timestamp"])
                task["duration"] = (task["end"] - task["start"]).total_seconds()
                if log["is_task_completed"]:
                    tasks_completed += 1
                else:
                    cancels += 1
                break

    # Erreurs
    if log_type == "error":
        errors += 1

    # Fréquence des fonctionnalités
    if log_type == "task_start" and details.get("name") in key_features:
        feature_usage[details["name"]] = feature_usage.get(details["name"], 0) + 1

    # Clics redondants
    if log_type == "click" and log["task_id"]:
        clicks_per_task[log["task_id"]] = clicks_per_task.get(log["task_id"], 0) + 1

    # Utilisation de l’aide
    if log_type == "help":
        help_usage += 1

    # Raccourcis clavier
    if log_type == "shortcut":
        shortcuts += 1

    # Temps de réponse aux alertes
    if log_type == "alert_response" and "response_time" in details:
        alert_responses.append(float(details["response_time"].replace("s", "")))

# Écoute des messages Redis
def listen_to_redis():
    for message in pubsub.listen():
        if message["type"] == "message":
            log = json.loads(message["data"])
            log_queue.put(log)
            process_log(log)

# Initialisation de Flask
app = Flask(__name__)

# Endpoints pour les métriques
@app.route("/metrics/task-execution-time", methods=["GET"])
def get_task_execution_time():
    df_tasks = pd.DataFrame([t for t in task_times if "duration" in t])
    if not df_tasks.empty:
        avg_times = df_tasks.groupby("name")["duration"].mean().to_dict()
        return jsonify({
            "average_times": {k: {"avg": v, "expert_avg": expert_avg.get(k, None)} for k, v in avg_times.items()},
            "total_tasks": len(df_tasks)
        })
    return jsonify({"average_times": {}, "total_tasks": 0})

@app.route("/metrics/error-cancel-rate", methods=["GET"])
def get_error_cancel_rate():
    error_rate = (errors / total_actions * 100) if total_actions > 0 else 0
    cancel_rate = (cancels / total_actions * 100) if total_actions > 0 else 0
    return jsonify({"error_rate": error_rate, "cancel_rate": cancel_rate, "total_actions": total_actions})

@app.route("/metrics/feature-usage", methods=["GET"])
def get_feature_usage():
    total_features = sum(feature_usage.values())
    return jsonify({
        "features": {
            feature: {"count": count, "percentage": (count / total_features * 100) if total_features > 0 else 0}
            for feature, count in feature_usage.items()
        },
        "total": total_features
    })

@app.route("/metrics/redundant-clicks", methods=["GET"])
def get_redundant_clicks():
    redundant_data = []
    for task_id, click_count in clicks_per_task.items():
        task_name = next((t["name"] for t in task_times if t["task_id"] == task_id), "unknown")
        optimal = optimal_clicks.get(task_name, 0)
        redundant_data.append({
            "task_id": task_id,
            "task_name": task_name,
            "clicks": click_count,
            "optimal": optimal,
            "redundant": max(0, click_count - optimal)
        })
    return jsonify({"redundant_clicks": redundant_data})

@app.route("/metrics/help-usage", methods=["GET"])
def get_help_usage():
    help_percentage = (help_usage / total_actions * 100) if total_actions > 0 else 0
    return jsonify({"count": help_usage, "percentage": help_percentage, "total_actions": total_actions})

@app.route("/metrics/task-completion-rate", methods=["GET"])
def get_task_completion_rate():
    completion_rate = (tasks_completed / tasks_started * 100) if tasks_started > 0 else 0
    return jsonify({
        "completion_rate": completion_rate,
        "tasks_started": tasks_started,
        "tasks_completed": tasks_completed
    })

@app.route("/metrics/shortcut-usage", methods=["GET"])
def get_shortcut_usage():
    shortcut_percentage = (shortcuts / total_actions * 100) if total_actions > 0 else 0
    return jsonify({"count": shortcuts, "percentage": shortcut_percentage, "total_actions": total_actions})

@app.route("/metrics/alert-response-time", methods=["GET"])
def get_alert_response_time():
    if alert_responses:
        avg_response = sum(alert_responses) / len(alert_responses)
        return jsonify({"average_response_time": avg_response, "responses": alert_responses})
    return jsonify({"average_response_time": 0, "responses": []})

# Démarrer l’écoute Redis dans un thread
thread = threading.Thread(target=listen_to_redis, daemon=True)
thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)