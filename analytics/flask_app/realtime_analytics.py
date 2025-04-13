from flask import Flask, jsonify, request
import redis
import json
import pandas as pd
import numpy as np
import time
from collections import defaultdict
import threading
from threading import Lock
import logging
from datetime import datetime, timedelta, timezone
import traceback
import statistics
from urllib.parse import urlparse
import re

app = Flask(__name__)

# Configurer le logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('realtime_analytics.log')
    ]
)
logger = logging.getLogger(__name__)

# Connexion à Redis avec gestion d'erreur
def connect_to_redis():
    max_retries = 5
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            client = redis.Redis(
                host='172.22.198.52',
                port=6379,
                decode_responses=True
            )
            client.ping()
            logger.info("Connexion à Redis réussie")
            return client
        except redis.ConnectionError as e:
            logger.error(f"Erreur de connexion à Redis (tentative {attempt + 1}/{max_retries}) : {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise Exception("Impossible de se connecter à Redis après plusieurs tentatives")

# Initialisation de Redis
try:
    redis_client = connect_to_redis()
    pubsub = redis_client.pubsub()
    pubsub.subscribe('logs:realtime')
except Exception as e:
    logger.error(f"Erreur fatale lors de la connexion à Redis : {e}")
    exit(1)

# Structure des métriques avec verrou pour éviter les problèmes de concurrence
metrics = {
    'task_times': [],
    'errors': 0,
    'cancellations': 0,
    'total_actions': 0,
    'redundant_actions': defaultdict(int),
    'help_usage': 0,
    'tutorial_usage': 0,
    'feature_usage': defaultdict(int),
    'tasks_started': 0,
    'tasks_completed': 0,
    'shortcut_usage': 0,
    'alert_times': [],
    'last_action': defaultdict(dict),
    'page_engagement': [],
    'interactions_by_element': defaultdict(int),
    'network_requests': [],
    'by_domain': defaultdict(lambda: defaultdict(int)),
    'user_sessions': defaultdict(list),  
    'session_activity': [],
    'user_activity': [],
    'error_details': [],
}
metrics_lock = Lock()

def convert_strings_to_timestamps(obj):
    if isinstance(obj, str):
        try:
            # Try to parse the string as a datetime
            return pd.to_datetime(obj)
        except (ValueError, TypeError):
            return obj  # If it's not a datetime string, return as-is
    elif isinstance(obj, dict):
        return {k: convert_strings_to_timestamps(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_strings_to_timestamps(item) for item in obj]
    elif isinstance(obj, set):
        return {convert_strings_to_timestamps(item) for item in obj}
    return obj

def load_metrics():
    try:
        with metrics_lock:
            metrics['task_times'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:task_times') or '[]'))
            metrics['errors'] = int(redis_client.get('metrics:errors') or 0)
            metrics['cancellations'] = int(redis_client.get('metrics:cancellations') or 0)
            metrics['total_actions'] = int(redis_client.get('metrics:total_actions') or 0)
            raw_redundant = json.loads(redis_client.get('metrics:redundant_actions') or '{}')
            cleaned_redundant = {}
            for key, value in raw_redundant.items():
                parts = key.split(':')
                if len(parts) < 6:
                    logger.warning(f"Clé mal formée supprimée de redundant_actions : {key}")
                    continue
                try:
                    timestamp = ':'.join(parts[5:])
                    pd.to_datetime(timestamp)
                    cleaned_redundant[key] = value
                except Exception as e:
                    logger.warning(f"Timestamp invalide dans redundant_actions, clé supprimée : {key}, erreur : {e}")
                    continue
            metrics['redundant_actions'] = cleaned_redundant
            metrics['help_usage'] = int(redis_client.get('metrics:help_usage') or 0)
            metrics['tutorial_usage'] = int(redis_client.get('metrics:tutorial_usage') or 0)
            metrics['feature_usage'] = json.loads(redis_client.get('metrics:feature_usage') or '{}')
            metrics['tasks_started'] = int(redis_client.get('metrics:tasks_started') or 0)
            metrics['tasks_completed'] = int(redis_client.get('metrics:tasks_completed') or 0)
            metrics['shortcut_usage'] = int(redis_client.get('metrics:shortcut_usage') or 0)
            metrics['alert_times'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:alert_times') or '[]'))
            metrics['last_action'] = json.loads(redis_client.get('metrics:last_action') or '{}')
            metrics['page_engagement'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:page_engagement') or '[]'))
            metrics['interactions_by_element'] = json.loads(redis_client.get('metrics:interactions_by_element') or '{}')
            metrics['network_requests'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:network_requests') or '[]'))
            raw_by_domain = json.loads(redis_client.get('metrics:by_domain') or '{}')
            by_domain = defaultdict(lambda: defaultdict(int))
            by_domain.update(raw_by_domain)
            metrics['by_domain'] = by_domain
            # Charger user_sessions et s'assurer que c'est un defaultdict(list)
            raw_user_sessions = json.loads(redis_client.get('metrics:user_sessions') or '{}')
            user_sessions = defaultdict(list)
            user_sessions.update(raw_user_sessions)
            metrics['user_sessions'] = user_sessions
            metrics['session_activity'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:session_activity') or '[]'))
            metrics['user_activity'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:user_activity') or '[]'))
            metrics['error_details'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:error_details') or '[]'))
        logger.info("Métriques chargées depuis Redis")
    except Exception as e:
        logger.error(f"Erreur lors du chargement des métriques : {e}")

# Sauvegarder les métriques dans Redis
def convert_timestamps_to_strings(obj):
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: convert_timestamps_to_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_timestamps_to_strings(item) for item in obj]
    elif isinstance(obj, set):
        return {convert_timestamps_to_strings(item) for item in obj}
    return obj

def save_metrics():
    try:
        with metrics_lock:
            metrics_serializable = convert_timestamps_to_strings(metrics)
            redis_client.set('metrics:task_times', json.dumps(metrics_serializable['task_times']))
            redis_client.set('metrics:errors', metrics_serializable['errors'])
            redis_client.set('metrics:cancellations', metrics_serializable['cancellations'])
            redis_client.set('metrics:total_actions', metrics_serializable['total_actions'])
            redis_client.set('metrics:redundant_actions', json.dumps(metrics_serializable['redundant_actions']))
            redis_client.set('metrics:help_usage', metrics_serializable['help_usage'])
            redis_client.set('metrics:tutorial_usage', metrics_serializable['tutorial_usage'])
            redis_client.set('metrics:feature_usage', json.dumps(metrics_serializable['feature_usage']))
            redis_client.set('metrics:tasks_started', metrics_serializable['tasks_started'])
            redis_client.set('metrics:tasks_completed', metrics_serializable['tasks_completed'])
            redis_client.set('metrics:shortcut_usage', metrics_serializable['shortcut_usage'])
            redis_client.set('metrics:alert_times', json.dumps(metrics_serializable['alert_times']))
            redis_client.set('metrics:last_action', json.dumps(metrics_serializable['last_action']))
            redis_client.set('metrics:page_engagement', json.dumps(metrics_serializable['page_engagement']))
            redis_client.set('metrics:interactions_by_element', json.dumps(metrics_serializable['interactions_by_element']))
            redis_client.set('metrics:network_requests', json.dumps(metrics_serializable['network_requests']))
            redis_client.set('metrics:by_domain', json.dumps(metrics_serializable['by_domain']))
            redis_client.set('metrics:user_sessions', json.dumps(metrics_serializable['user_sessions']))
            redis_client.set('metrics:session_activity', json.dumps(metrics_serializable['session_activity']))
            redis_client.set('metrics:user_activity', json.dumps(metrics_serializable['user_activity']))
            redis_client.set('metrics:error_details', json.dumps(metrics_serializable['error_details']))
        logger.info("Métriques sauvegardées dans Redis")
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde des métriques : {e}")

def save_metrics_transactional():
    try:
        pipe = redis_client.pipeline()
        with metrics_lock:
            # Convert Timestamps to strings before saving
            metrics_serializable = convert_timestamps_to_strings(metrics)
            pipe.set('metrics:task_times', json.dumps(metrics_serializable['task_times']))
            pipe.expire('metrics:task_times', 3600)
            pipe.set('metrics:errors', metrics_serializable['errors'])
            pipe.set('metrics:cancellations', metrics_serializable['cancellations'])
            pipe.set('metrics:total_actions', metrics_serializable['total_actions'])
            pipe.set('metrics:redundant_actions', json.dumps(metrics_serializable['redundant_actions']))
            pipe.expire('metrics:redundant_actions', 3600)
            pipe.set('metrics:help_usage', metrics_serializable['help_usage'])
            pipe.set('metrics:tutorial_usage', metrics_serializable['tutorial_usage'])
            pipe.set('metrics:feature_usage', json.dumps(metrics_serializable['feature_usage']))
            pipe.expire('metrics:feature_usage', 3600)
            pipe.set('metrics:tasks_started', metrics_serializable['tasks_started'])
            pipe.set('metrics:tasks_completed', metrics_serializable['tasks_completed'])
            pipe.set('metrics:shortcut_usage', metrics_serializable['shortcut_usage'])
            pipe.set('metrics:alert_times', json.dumps(metrics_serializable['alert_times']))
            pipe.expire('metrics:alert_times', 3600)
            pipe.set('metrics:last_action', json.dumps(metrics_serializable['last_action']))
            pipe.expire('metrics:last_action', 3600)
            pipe.set('metrics:page_engagement', json.dumps(metrics_serializable['page_engagement']))
            pipe.expire('metrics:page_engagement', 3600)
            pipe.set('metrics:interactions_by_element', json.dumps(metrics_serializable['interactions_by_element']))
            pipe.expire('metrics:interactions_by_element', 3600)
            pipe.set('metrics:network_requests', json.dumps(metrics_serializable['network_requests']))
            pipe.expire('metrics:network_requests', 3600)
            pipe.set('metrics:by_domain', json.dumps(metrics_serializable['by_domain']))
            pipe.expire('metrics:by_domain', 3600)
            pipe.set('metrics:user_sessions', json.dumps(metrics_serializable['user_sessions']))
            pipe.expire('metrics:user_sessions', 3600)
            pipe.set('metrics:session_activity', json.dumps(metrics_serializable['session_activity']))
            pipe.expire('metrics:session_activity', 3600)
            pipe.set('metrics:user_activity', json.dumps(metrics_serializable['user_activity']))
            pipe.expire('metrics:user_activity', 3600)
            pipe.set('metrics:error_details', json.dumps(metrics_serializable['error_details']))
            pipe.expire('metrics:error_details', 3600)
        pipe.execute()
        logger.debug("Métriques sauvegardées de manière transactionnelle")
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde transactionnelle des métriques : {e}")
# Sauvegarde périodique (toutes les 60 secondes)
def periodic_save():
    while True:
        save_metrics()
        time.sleep(60)

# Nettoyage périodique (toutes les 5 minutes)
def periodic_cleanup():
    while True:
        cleanup_metrics()
        logger.info("Nettoyage des métriques effectué")
        time.sleep(300)

# Validation des logs
def validate_log(log):
    required_fields = ['timestamp', 'type', 'user_id', 'session_id']
    for field in required_fields:
        if field not in log:
            raise ValueError(f"Champ requis manquant : {field}")
    try:
        pd.to_datetime(log['timestamp'])
    except Exception as e:
        raise ValueError(f"Timestamp invalide : {log['timestamp']}, erreur : {e}")
    valid_types = [
        'task_start', 'task_end', 'error', 'cancel', 'click', 'input',
        'help_access', 'tutorial_access', 'shortcut', 'alert_shown', 'alert_response',
        'network', 'network-error', 'page_engagement'
    ]
    if log['type'] not in valid_types:
        raise ValueError(f"Type de log invalide : {log['type']}")
    # Vérification des détails spécifiques
    if 'details' not in log:
        log['details'] = {}
    # Vérification des champs numériques
    if log['type'] == 'page_engagement':
        try:
            float(log['details'].get('active_time', '0s').replace('s', ''))
            float(log['details'].get('max_scroll_depth', '0%').replace('%', ''))
        except (ValueError, TypeError) as e:
            raise ValueError(f"Valeurs invalides dans page_engagement : {e}")
    return True

# Nettoyage des données
def cleanup_metrics():
    current_time = pd.to_datetime(datetime.utcnow())
    cutoff_24h = current_time - pd.Timedelta(hours=24)
    cutoff_5min = current_time - pd.Timedelta(minutes=5)
    with metrics_lock:
        # Nettoyage des tâches
        metrics['task_times'] = [
            task for task in metrics['task_times']
            if ('duration' not in task and (current_time - task['start']).total_seconds() < 900)  # 15 minutes
            or ('duration' in task and task['start'] > cutoff_24h)
        ]
        # Nettoyage des alertes
        metrics['alert_times'] = [
            alert for alert in metrics['alert_times']
            if ('response_time' not in alert and (current_time - alert['start']).total_seconds() < 900)
            or ('response_time' in alert and alert['start'] > cutoff_24h)
        ]
        # Nettoyage des requêtes réseau (24h)
        metrics['network_requests'] = [
            req for req in metrics['network_requests']
            if pd.to_datetime(req['timestamp']) > cutoff_24h
        ]
        # Nettoyage des données d'engagement (24h)
        metrics['page_engagement'] = [
            eng for eng in metrics['page_engagement']
            if pd.to_datetime(eng['timestamp']) > cutoff_24h
        ]
        # Nettoyage des sessions (24h)
        metrics['session_activity'] = [
            session for session in metrics['session_activity']
            if pd.to_datetime(session['start']) > cutoff_24h
        ]
        # Nettoyage des activités utilisateur (24h)
        metrics['user_activity'] = [
            activity for activity in metrics['user_activity']
            if pd.to_datetime(activity['timestamp']) > cutoff_24h
        ]
        # Nettoyage des détails des erreurs (24h)
        metrics['error_details'] = [
            error for error in metrics['error_details']
            if pd.to_datetime(error['timestamp']) > cutoff_24h
        ]

def process_log(log):
    try:
        validate_log(log)
        user_id = log.get('user_id')
        session_id = log.get('session_id')
        log_type = log.get('type')
        details = log.get('details', {})
        domain = log.get('domain', 'unknown')
        if domain == 'unknown' and 'url' in log:
            parsed_url = urlparse(log['url'])
            domain = parsed_url.netloc or 'unknown'
            if domain.startswith('www.'):
                domain = domain[4:]
        log['domain'] = domain

        # Sanitize task name
        task_name = details.get('name', 'unknown')
        # Check if the task name looks like a URL
        if task_name.startswith('http://') or task_name.startswith('https://'):
            logger.warning(f"Task name appears to be a URL: {task_name}. Attempting to infer task type.")
            # Infer task name based on context (e.g., form submission)
            if log_type == 'task_start' or log_type == 'task_end':
                if 'form' in log['url'].lower():
                    task_name = 'form_submit'
                else:
                    task_name = 'unknown_task'
            else:
                task_name = 'unknown'
        details['name'] = task_name

        with metrics_lock:
            metrics['total_actions'] += 1
            metrics['by_domain'][domain]['total_actions'] += 1

            if session_id not in metrics['user_sessions'][user_id]:
                metrics['user_sessions'][user_id].append(session_id)
                metrics['session_activity'].append({
                    'user_id': user_id,
                    'session_id': session_id,
                    'start': pd.to_datetime(log['timestamp']),
                    'actions': 0,
                    'domains': set(),
                    'features': set(),
                    'errors': 0
                })
            for session in metrics['session_activity']:
                if session['user_id'] == user_id and session['session_id'] == session_id:
                    session['actions'] += 1
                    session['domains'].add(domain)
                    session['features'].add(details.get('feature', 'N/A'))
                    if log_type in ['error', 'network-error']:
                        session['errors'] += 1
                    if 'end' not in session:
                        session['duration'] = (pd.to_datetime(log['timestamp']) - session['start']).total_seconds()

            task_id = log.get('task_id')
            if task_id:
                if log_type == 'task_start':
                    metrics['tasks_started'] += 1
                    task_entry = {
                        'task_id': task_id,
                        'user_id': user_id,
                        'session_id': session_id,
                        'domain': domain,
                        'name': task_name,  # Use sanitized task name
                        'start': pd.to_datetime(log['timestamp']),
                        'is_completed': False
                    }
                    metrics['task_times'].append(task_entry)
                    logger.debug(f"Tâche démarrée : {task_entry}")
                elif log_type == 'task_end':
                    for task in metrics['task_times']:
                        if task['task_id'] == task_id and not task['is_completed']:
                            task['is_completed'] = details.get('is_task_completed', False)
                            task['end'] = pd.to_datetime(log['timestamp'])
                            task['duration'] = (task['end'] - task['start']).total_seconds()
                            task['name'] = task_name  # Update task name if necessary
                            logger.debug(f"Tâche terminée : {task}")
                            break
                    else:
                        logger.warning(f"task_end reçu pour une tâche inconnue ou déjà terminée : task_id={task_id}")

            metrics['user_activity'].append({
                'user_id': user_id,
                'session_id': session_id,
                'timestamp': log['timestamp'],
                'type': log_type,
                'domain': domain,
                'feature': details.get('feature', 'N/A'),
                'details': details
            })
            logger.debug(f"Activité ajoutée à user_activity : {metrics['user_activity'][-1]}")
        save_metrics_transactional()
    except Exception as e:
        logger.error(f"Erreur traitement log : {e}\nTrace: {traceback.format_exc()}\nLog: {log}")
        redis_client.lpush('errors', f"{str(e)}: {json.dumps(log)}") 
        
# Écoute Redis
def listen_redis():
    logger.info("Démarrage de l'écoute Redis sur le canal logs:realtime")
    for message in pubsub.listen():
        if message['type'] == 'message':
            try:
                log = json.loads(message['data'])
                process_log(log)
            except Exception as e:
                logger.error(f"Erreur traitement log : {e}\nTrace: {traceback.format_exc()}\nMessage: {message['data']}")
                redis_client.lpush('errors', f"{str(e)}: {message['data']}")

# Fonction utilitaire pour calculer des statistiques avancées
def compute_advanced_stats(records, key):
    values = [r[key] for r in records if key in r]
    if not values:
        return {}
    all_zero = all(v == 0 for v in values)
    result = {
        'count': len(values),
        'mean': round(float(np.mean(values)), 2),
        'median': round(float(np.median(values)), 2),
        'min': round(float(np.min(values)), 2),
        'max': round(float(np.max(values)), 2),
        'std': round(float(np.std(values)), 2) if len(values) > 1 else 0
    }
    if all_zero:
        result['note'] = "All durations are 0, possibly due to simultaneous task_start and task_end events."
    return result

# 1. Analyse des temps d'exécution des tâches
@app.route('/metrics/task-execution-time', methods=['GET'])
def get_task_execution_time():
    domain_filter = request.args.get('domain')
    user_id_filter = request.args.get('user_id')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            hours = int(time_window[:-1])
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=hours)
        elif time_window.endswith('d'):
            days = int(time_window[:-1])
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=days)
        else:
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=24)

    with metrics_lock:
        logger.debug(f"Contenu de metrics['task_times'] avant filtrage : {metrics['task_times']}")
        completed_tasks = [
            t for t in metrics['task_times']
            if 'duration' in t and t['start'] >= cutoff
            and (domain_filter is None or t['domain'] == domain_filter)
            and (user_id_filter is None or t['user_id'] == user_id_filter)
        ]
        ongoing_tasks = [
            t for t in metrics['task_times']
            if 'duration' not in t and t['start'] >= cutoff
            and (domain_filter is None or t['domain'] == domain_filter)
            and (user_id_filter is None or t['user_id'] == user_id_filter)
        ]
        logger.debug(f"Tâches complétées après filtrage : {completed_tasks}")
        logger.debug(f"Tâches en cours après filtrage : {ongoing_tasks}")

        df_tasks = pd.DataFrame(completed_tasks)
        stats = {}
        if not df_tasks.empty:
            stats = df_tasks.groupby('name', group_keys=False).apply(
                lambda x: compute_advanced_stats(x.to_dict('records'), 'duration'),
                include_groups=False
            ).to_dict()

        current_time = pd.Timestamp.now(tz='UTC')
        logger.debug(f"Heure actuelle (current_time) : {current_time}")
        abandoned_tasks = [
            t for t in ongoing_tasks
            if (current_time - t['start']).total_seconds() >= 900
        ]
        abandoned_rate = (len(abandoned_tasks) / metrics['tasks_started'] * 100) if metrics['tasks_started'] > 0 else 0

        by_domain = {}
        if not df_tasks.empty:
            grouped = df_tasks.groupby(['domain', 'name'], group_keys=False).apply(
                lambda x: compute_advanced_stats(x.to_dict('records'), 'duration'),
                include_groups=False
            )
            for (domain, name), stat in grouped.items():
                if domain not in by_domain:
                    by_domain[domain] = {}
                by_domain[domain][name] = stat

        ongoing_details = [
            {
                'task_id': t['task_id'],
                'name': t['name'],
                'start': t['start'].isoformat() if not pd.isna(t['start']) else "NaT",
                'elapsed_seconds': (current_time - t['start']).total_seconds(),
                'domain': t['domain'],
                'user_id': t['user_id']
            } for t in ongoing_tasks
        ]

    return jsonify({
        'stats': stats,
        'by_domain': by_domain,
        'total_completed': len(completed_tasks),
        'total_ongoing': len(ongoing_tasks),
        'total_abandoned': len(abandoned_tasks),
        'abandoned_rate': round(abandoned_rate, 2),
        'total_started': metrics['tasks_started'],
        'ongoing_details': ongoing_details
    })
    
# 2. Analyse des erreurs et annulations
@app.route('/metrics/errors-and-cancellations', methods=['GET'])
def get_errors_and_cancellations():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        errors_filtered = [
            e for e in metrics['error_details']
            if pd.to_datetime(e['timestamp']) >= cutoff
            and (domain_filter is None or e['domain'] == domain_filter)
        ]
        total_actions_filtered = sum(
            1 for a in metrics['user_activity']
            if pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ) or 1  # Éviter division par 0
        error_rate = (len(errors_filtered) / total_actions_filtered * 100)
        cancellations_filtered = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'cancel' and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        )
        cancellation_rate = (cancellations_filtered / total_actions_filtered * 100)

        # Analyse par type d'erreur
        df_errors = pd.DataFrame(errors_filtered)
        error_types = df_errors.groupby('type').size().to_dict() if not df_errors.empty else {}
        error_messages = df_errors.groupby('message').size().to_dict() if not df_errors.empty else {}

        # Analyse par domaine
        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_errors = len([
                e for e in errors_filtered if e['domain'] == domain
            ])
            domain_actions = sum(
                1 for a in metrics['user_activity']
                if a['domain'] == domain and pd.to_datetime(a['timestamp']) >= cutoff
            ) or 1
            domain_cancellations = sum(
                1 for a in metrics['user_activity']
                if a['type'] == 'cancel' and a['domain'] == domain and pd.to_datetime(a['timestamp']) >= cutoff
            )
            by_domain[domain] = {
                'errors': domain_errors,
                'error_rate': round((domain_errors / domain_actions * 100), 2),
                'cancellations': domain_cancellations,
                'cancellation_rate': round((domain_cancellations / domain_actions * 100), 2)
            }

    return jsonify({
        'errors': len(errors_filtered),
        'error_rate': round(error_rate, 2),
        'error_types': error_types,
        'error_messages': error_messages,
        'cancellations': cancellations_filtered,
        'cancellation_rate': round(cancellation_rate, 2),
        'total_actions': total_actions_filtered,
        'by_domain': by_domain
    })


# 3. Analyse des actions redondantes
@app.route('/metrics/redundant-actions', methods=['GET'])
def get_redundant_actions():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        total_actions_filtered = sum(
            1 for a in metrics['user_activity']
            if pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ) or 1
        total_redundant = 0
        redundant_by_type = defaultdict(int)
        by_domain = {}

        for key, count in metrics['redundant_actions'].items():
            parts = key.split(':')
            if len(parts) < 5:  
                logger.warning(f"Clé mal formée dans redundant_actions : {key}")
                continue
            user_id, session_id, action_type, action_detail, timestamp = parts[0], parts[1], parts[2], parts[3], ':'.join(parts[4:])
            try:
                action_time = pd.to_datetime(timestamp)
            except Exception as e:
                logger.error(f"Erreur lors du parsing du timestamp dans redundant_actions : {timestamp}, erreur : {e}")
                continue
            # Trouver le domaine associé via user_activity
            domain = None
            for activity in metrics['user_activity']:
                if activity['user_id'] == user_id and activity['session_id'] == session_id and activity['timestamp'] == timestamp:
                    domain = activity['domain']
                    break
            if domain is None:
                logger.warning(f"Domaine non trouvé pour la clé : {key}")
                continue
            if (domain_filter is None or domain == domain_filter) and action_time >= cutoff:
                total_redundant += count
                redundant_by_type[action_type] += count
                # Mise à jour des métriques par domaine
                if domain not in by_domain:
                    domain_actions = sum(
                        1 for a in metrics['user_activity']
                        if a['domain'] == domain and pd.to_datetime(a['timestamp']) >= cutoff
                    ) or 1
                    by_domain[domain] = {
                        'total_redundant': 0,
                        'redundant_rate': 0.0,
                        'domain_actions': domain_actions
                    }
                by_domain[domain]['total_redundant'] += count

        # Calculer les taux par domaine
        for domain in by_domain:
            by_domain[domain]['redundant_rate'] = round(
                (by_domain[domain]['total_redundant'] / by_domain[domain]['domain_actions'] * 100), 2
            )
            del by_domain[domain]['domain_actions']  # Supprimer le champ temporaire

        redundant_rate = (total_redundant / total_actions_filtered * 100)

    return jsonify({
        'redundant_actions': dict(redundant_by_type),
        'total_redundant': total_redundant,
        'redundant_rate': round(redundant_rate, 2),
        'total_actions': total_actions_filtered,
        'by_domain': by_domain
    })
    
# 4. Analyse de l'utilisation de l'aide et des tutoriels
@app.route('/metrics/help-and-tutorials', methods=['GET'])
def get_help_and_tutorials():
    domain_filter = request.args.get('domain')
    user_id_filter = request.args.get('user_id')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            hours = int(time_window[:-1])
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=hours)
        elif time_window.endswith('d'):
            days = int(time_window[:-1])
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=days)
        else:
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=24)

    help_keywords = [
        'help', 'aide', 'assistance', 'support', 'faq', 'question',
        'tutoriel', 'tutorial', 'guide', 'visite guidee', 'guided tour',
        'demarrer', 'get started', 'introduction', 'intro', 'onboarding',
        'apprendre', 'learn', 'formation', 'training', 'explication', 'explain'
    ]

    def normalize_text(text):
        text = text.lower()
        text = re.sub(r'\s+', ' ', text.strip())
        text = text.replace('é', 'e').replace('è', 'e').replace('ê', 'e').replace('ë', 'e')
        return text

    def is_help_related(activity):
        logger.debug(f"Analyse de l'activité : {activity}")
        if activity['type'] == 'help_access':
            return True, 'help'
        if activity['type'] == 'tutorial_access':
            return True, 'tutorial'

        details = activity.get('details', {})
        text = normalize_text(str(details.get('text', '')))
        element = normalize_text(str(details.get('element', '')))
        feature = normalize_text(str(details.get('feature', '')))
        logger.debug(f"Texte normalisé : text={text}, element={element}, feature={feature}")

        for keyword in help_keywords:
            normalized_keyword = normalize_text(keyword)
            if normalized_keyword in text or normalized_keyword in element or normalized_keyword in feature:
                logger.debug(f"Mot-clé trouvé : {normalized_keyword}")
                if normalized_keyword in ['help', 'aide', 'assistance', 'support', 'faq', 'question']:
                    return True, 'help'
                if normalized_keyword in ['tutoriel', 'tutorial', 'guide', 'visite guidee', 'guided tour']:
                    return True, 'tutorial'
                if normalized_keyword in ['demarrer', 'get started', 'introduction', 'intro', 'onboarding']:
                    return True, 'onboarding'
                if normalized_keyword in ['apprendre', 'learn', 'formation', 'training', 'explication', 'explain']:
                    return True, 'learning'
                return True, 'other'
        return False, None

    with metrics_lock:
        filtered_activities = [
            a for a in metrics['user_activity']
            if pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
            and (user_id_filter is None or a['user_id'] == user_id_filter)
        ]
        logger.debug(f"Activités filtrées : {filtered_activities}")
        total_actions_filtered = len(filtered_activities) or 1

        help_usage_by_category = defaultdict(int)
        help_users = set()
        help_activities = []

        for activity in filtered_activities:
            is_related, category = is_help_related(activity)
            logger.debug(f"Résultat is_help_related : is_related={is_related}, category={category}")
            if is_related:
                help_usage_by_category[category] += 1
                logger.debug(f"Catégorie ajoutée : {category}, nouveau compte : {help_usage_by_category[category]}")
                help_users.add(activity['user_id'])
                help_activities.append(activity)

        total_help_usage = sum(help_usage_by_category.values())
        total_help_rate = (total_help_usage / total_actions_filtered * 100) if total_actions_filtered > 0 else 0

        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_activities = [
                a for a in filtered_activities
                if a['domain'] == domain
            ]
            domain_actions = len(domain_activities) or 1
            domain_help_by_category = defaultdict(int)
            for activity in domain_activities:
                is_related, category = is_help_related(activity)
                if is_related:
                    domain_help_by_category[category] += 1
            domain_total_help = sum(domain_help_by_category.values())
            by_domain[domain] = {
                'help_usage_by_category': dict(domain_help_by_category),
                'total_help_usage': domain_total_help,
                'help_usage_rate': round((domain_total_help / domain_actions * 100), 2)
            }

        by_user = {}
        for user_id in set(a['user_id'] for a in filtered_activities):
            if user_id_filter and user_id != user_id_filter:
                continue
            user_activities = [
                a for a in filtered_activities
                if a['user_id'] == user_id
            ]
            user_actions = len(user_activities) or 1
            user_help_by_category = defaultdict(int)
            for activity in user_activities:
                is_related, category = is_help_related(activity)
                if is_related:
                    user_help_by_category[category] += 1
            user_total_help = sum(user_help_by_category.values())
            by_user[user_id] = {
                'help_usage_by_category': dict(user_help_by_category),
                'total_help_usage': user_total_help,
                'help_usage_rate': round((user_total_help / user_actions * 100), 2)
            }

        by_hour = defaultdict(lambda: defaultdict(int))
        for activity in help_activities:
            timestamp = pd.to_datetime(activity['timestamp'])
            hour = timestamp.strftime('%Y-%m-%d %H:00:00')
            is_related, category = is_help_related(activity)
            by_hour[hour][category] += 1

    return jsonify({
        'help_usage_by_category': dict(help_usage_by_category),
        'total_help_usage': total_help_usage,
        'total_help_rate': round(total_help_rate, 2),
        'total_actions': total_actions_filtered,
        'unique_users': len(help_users),
        'by_domain': by_domain,
        'by_user': by_user,
        'by_hour': {hour: dict(categories) for hour, categories in by_hour.items()}
    })

# 5. Analyse du taux de complétion des tâches
@app.route('/metrics/task-completion-rate', methods=['GET'])
def get_task_completion_rate():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        tasks_started = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'task_start' and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        )
        tasks_completed = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'task_end' and a['details'].get('is_task_completed', False)
            and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        )
        completion_rate = (tasks_completed / tasks_started * 100) if tasks_started > 0 else 0

        # Analyse par domaine
        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_started = sum(
                1 for a in metrics['user_activity']
                if a['type'] == 'task_start' and a['domain'] == domain and pd.to_datetime(a['timestamp']) >= cutoff
            )
            domain_completed = sum(
                1 for a in metrics['user_activity']
                if a['type'] == 'task_end' and a['details'].get('is_task_completed', False)
                and a['domain'] == domain and pd.to_datetime(a['timestamp']) >= cutoff
            )
            by_domain[domain] = {
                'tasks_started': domain_started,
                'tasks_completed': domain_completed,
                'completion_rate': round((domain_completed / domain_started * 100) if domain_started > 0 else 0, 2)
            }

    return jsonify({
        'completion_rate': round(completion_rate, 2),
        'tasks_started': tasks_started,
        'tasks_completed': tasks_completed,
        'by_domain': by_domain
    })

# 6. Analyse de l'utilisation des raccourcis clavier
@app.route('/metrics/shortcut-usage', methods=['GET'])
def get_shortcut_usage():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        total_actions_filtered = sum(
            1 for a in metrics['user_activity']
            if pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ) or 1
        shortcut_usage = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'shortcut' and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        )
        shortcut_rate = (shortcut_usage / total_actions_filtered * 100)

        # Analyse par combinaison de raccourcis
        df_shortcuts = pd.DataFrame([
            a for a in metrics['user_activity']
            if a['type'] == 'shortcut' and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ])
        shortcut_combinations = df_shortcuts.groupby('details.combination').size().to_dict() if not df_shortcuts.empty else {}

        # Analyse par domaine
        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_actions = sum(
                1 for a in metrics['user_activity']
                if a['domain'] == domain and pd.to_datetime(a['timestamp']) >= cutoff
            ) or 1
            domain_shortcuts = sum(
                1 for a in metrics['user_activity']
                if a['type'] == 'shortcut' and a['domain'] == domain and pd.to_datetime(a['timestamp']) >= cutoff
            )
            by_domain[domain] = {
                'shortcut_usage': domain_shortcuts,
                'shortcut_usage_rate': round((domain_shortcuts / domain_actions * 100), 2)
            }

    return jsonify({
        'shortcut_usage': shortcut_usage,
        'shortcut_usage_rate': round(shortcut_rate, 2),
        'shortcut_combinations': shortcut_combinations,
        'total_actions': total_actions_filtered,
        'by_domain': by_domain
    })

# 7. Analyse du temps de réponse aux alertes
@app.route('/metrics/alert-response-time', methods=['GET'])
def get_alert_response_time():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_alerts = pd.DataFrame([
            a for a in metrics['alert_times']
            if 'response_time' in a and a['start'] >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ])
        stats = compute_advanced_stats(df_alerts.to_dict('records'), 'response_time') if not df_alerts.empty else {}

        # Analyse par message
        by_message = df_alerts.groupby('message').apply(
            lambda x: compute_advanced_stats(x.to_dict('records'), 'response_time')
        ).to_dict() if not df_alerts.empty else {}

        # Analyse par domaine
        by_domain = df_alerts.groupby('domain').apply(
            lambda x: compute_advanced_stats(x.to_dict('records'), 'response_time')
        ).to_dict() if not df_alerts.empty else {}

        # Taux de réponse
        alerts_shown = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'alert_shown' and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        )
        response_rate = (len(df_alerts) / alerts_shown * 100) if alerts_shown > 0 else 0

    return jsonify({
        'stats': stats,
        'by_message': by_message,
        'by_domain': by_domain,
        'total_alerts': len(df_alerts),
        'response_rate': round(response_rate, 2)
    })

# 8. Analyse de l'utilisation des fonctionnalités
@app.route('/metrics/feature-usage', methods=['GET'])
def get_feature_usage():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_activity = pd.DataFrame([
            a for a in metrics['user_activity']
            if a['feature'] != 'N/A' and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ])
        feature_counts = df_activity.groupby('feature').size().to_dict() if not df_activity.empty else {}
        total_features = sum(feature_counts.values())
        feature_rates = {k: (v / total_features * 100) if total_features > 0 else 0 for k, v in feature_counts.items()}

        # Analyse par domaine
        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_features = df_activity[df_activity['domain'] == domain].groupby('feature').size().to_dict()
            domain_total = sum(domain_features.values())
            by_domain[domain] = {
                'features': domain_features,
                'feature_rates': {k: round((v / domain_total * 100) if domain_total > 0 else 0, 2) for k, v in domain_features.items()}
            }

    return jsonify({
        'features': feature_counts,
        'feature_rates': {k: round(v, 2) for k, v in feature_rates.items()},
        'total': total_features,
        'by_domain': by_domain
    })

# 9. Détails des tâches
@app.route('/metrics/task-details', methods=['GET'])
def get_task_details():
    domain_filter = request.args.get('domain')
    user_id_filter = request.args.get('user_id')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        completed_tasks = [
            {
                'task_id': t['task_id'],
                'name': t['name'],
                'start': t['start'].isoformat(),
                'end': t['end'].isoformat(),
                'duration': t['duration'],
                'domain': t['domain'],
                'user_id': t['user_id'],
                'session_id': t['session_id']
            } for t in metrics['task_times']
            if 'duration' in t and t['start'] >= cutoff
            and (domain_filter is None or t['domain'] == domain_filter)
            and (user_id_filter is None or t['user_id'] == user_id_filter)
        ]
        ongoing_tasks = [
            {
                'task_id': t['task_id'],
                'name': t['name'],
                'start': t['start'].isoformat(),
                'elapsed': (pd.to_datetime(datetime.utcnow()) - t['start']).total_seconds(),
                'domain': t['domain'],
                'user_id': t['user_id'],
                'session_id': t['session_id']
            } for t in metrics['task_times']
            if 'duration' not in t and t['start'] >= cutoff
            and (domain_filter is None or t['domain'] == domain_filter)
            and (user_id_filter is None or t['user_id'] == user_id_filter)
        ]
    return jsonify({
        'completed_tasks': completed_tasks,
        'ongoing_tasks': ongoing_tasks
    })

# 10. Analyse de l'engagement utilisateur
@app.route('/metrics/page-engagement', methods=['GET'])
def get_page_engagement():
    domain_filter = request.args.get('domain')
    user_id_filter = request.args.get('user_id')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_engagement = pd.DataFrame([
            e for e in metrics['page_engagement']
            if pd.to_datetime(e['timestamp']) >= cutoff
            and (domain_filter is None or e['domain'] == domain_filter)
            and (user_id_filter is None or e['user_id'] == user_id_filter)
        ])
        if df_engagement.empty:
            return jsonify({
                'active_time_stats': {},
                'scroll_depth_stats': {},
                'by_domain': {},
                'by_url': {},
                'total_pages': 0
            })

        active_time_stats = compute_advanced_stats(df_engagement.to_dict('records'), 'active_time')
        scroll_depth_stats = compute_advanced_stats(df_engagement.to_dict('records'), 'max_scroll_depth')

        # Analyse par domaine
        by_domain = df_engagement.groupby('domain').apply(lambda x: {
            'active_time_stats': compute_advanced_stats(x.to_dict('records'), 'active_time'),
            'scroll_depth_stats': compute_advanced_stats(x.to_dict('records'), 'max_scroll_depth'),
            'page_visits': len(x)
        }).to_dict()

        # Analyse par URL
        by_url = df_engagement.groupby('url').apply(lambda x: {
            'active_time_stats': compute_advanced_stats(x.to_dict('records'), 'active_time'),
            'scroll_depth_stats': compute_advanced_stats(x.to_dict('records'), 'max_scroll_depth'),
            'page_visits': len(x)
        }).to_dict()

    return jsonify({
        'active_time_stats': active_time_stats,
        'scroll_depth_stats': scroll_depth_stats,
        'by_domain': by_domain,
        'by_url': by_url,
        'total_pages': len(df_engagement)
    })

# 11. Analyse des types d'interaction
@app.route('/metrics/interaction-types', methods=['GET'])
def get_interaction_types():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_interactions = pd.DataFrame([
            a for a in metrics['user_activity']
            if a['type'] in ['click', 'input'] and pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ])
        if df_interactions.empty:
            return jsonify({
                'interactions': {},
                'interaction_rates': {},
                'by_domain': {},
                'total_interactions': 0
            })

        interactions = df_interactions.groupby('details.element').size().to_dict()
        total_interactions = sum(interactions.values())
        interaction_rates = {k: round((v / total_interactions * 100), 2) for k, v in interactions.items()}

        # Analyse par domaine
        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_interactions = df_interactions[df_interactions['domain'] == domain].groupby('details.element').size().to_dict()
            domain_total = sum(domain_interactions.values())
            by_domain[domain] = {
                'interactions': domain_interactions,
                'interaction_rates': {k: round((v / domain_total * 100) if domain_total > 0 else 0, 2) for k, v in domain_interactions.items()}
            }

    return jsonify({
        'interactions': interactions,
        'interaction_rates': interaction_rates,
        'by_domain': by_domain,
        'total_interactions': total_interactions
    })

# 12. Analyse des performances réseau
@app.route('/metrics/network-performance', methods=['GET'])
def get_network_performance():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_requests = pd.DataFrame([
            r for r in metrics['network_requests']
            if pd.to_datetime(r['timestamp']) >= cutoff
            and (domain_filter is None or r['domain'] == domain_filter)
        ])
        if df_requests.empty:
            return jsonify({
                'latency_stats': {},
                'error_rate': 0,
                'by_domain': {},
                'by_method': {},
                'total_requests': 0
            })

        latency_stats = compute_advanced_stats(df_requests.to_dict('records'), 'duration')
        total_requests = len(df_requests)
        error_requests = len(df_requests[df_requests['status'] >= 400])
        error_rate = (error_requests / total_requests * 100) if total_requests > 0 else 0

        # Analyse par domaine
        by_domain = df_requests.groupby('domain').apply(lambda x: {
            'latency_stats': compute_advanced_stats(x.to_dict('records'), 'duration'),
            'total_requests': len(x),
            'error_requests': len(x[x['status'] >= 400])
        }).to_dict()

        # Analyse par méthode
        by_method = df_requests.groupby('method').apply(lambda x: {
            'latency_stats': compute_advanced_stats(x.to_dict('records'), 'duration'),
            'total_requests': len(x),
            'error_requests': len(x[x['status'] >= 400])
        }).to_dict()

    return jsonify({
        'latency_stats': latency_stats,
        'error_rate': round(error_rate, 2),
        'by_domain': by_domain,
        'by_method': by_method,
        'total_requests': total_requests
    })

# 13. Analyse par domaine
@app.route('/metrics/by-domain', methods=['GET'])
def get_metrics_by_domain():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_activity = pd.DataFrame([
            a for a in metrics['user_activity']
            if pd.to_datetime(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ])
        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_activity = df_activity[df_activity['domain'] == domain]
            domain_actions = len(domain_activity)
            domain_errors = len(domain_activity[domain_activity['type'].isin(['error', 'network-error'])])
            domain_cancellations = len(domain_activity[domain_activity['type'] == 'cancel'])
            domain_redundant = sum(
                count for key, count in metrics['redundant_actions'].items()
                if key.split(':')[1] == domain and pd.to_datetime(key.split(':')[3]) >= cutoff
            )
            domain_help = len(domain_activity[domain_activity['type'] == 'help_access'])
            domain_tutorial = len(domain_activity[domain_activity['type'] == 'tutorial_access'])
            domain_shortcuts = len(domain_activity[domain_activity['type'] == 'shortcut'])
            domain_features = domain_activity.groupby('feature').size().to_dict()
            domain_page_visits = len([
                e for e in metrics['page_engagement']
                if e['domain'] == domain and pd.to_datetime(e['timestamp']) >= cutoff
            ])
            by_domain[domain] = {
                'total_actions': domain_actions,
                'errors': domain_errors,
                'error_rate': round((domain_errors / domain_actions * 100) if domain_actions > 0 else 0, 2),
                'cancellations': domain_cancellations,
                'redundant_actions': domain_redundant,
                'help_usage': domain_help,
                'tutorial_usage': domain_tutorial,
                'shortcut_usage': domain_shortcuts,
                'page_visits': domain_page_visits,
                'features': domain_features
            }
    return jsonify({'by_domain': by_domain})

# 14. Analyse des sessions utilisateur
@app.route('/metrics/user-sessions', methods=['GET'])
def get_user_sessions():
    user_id_filter = request.args.get('user_id')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_sessions = pd.DataFrame([
            s for s in metrics['session_activity']
            if s['start'] >= cutoff
            and (user_id_filter is None or s['user_id'] == user_id_filter)
        ])
        if df_sessions.empty:
            return jsonify({
                'session_stats': {},
                'by_user': {},
                'total_sessions': 0
            })

        session_stats = {
            'duration': compute_advanced_stats(df_sessions.to_dict('records'), 'duration'),
            'actions': compute_advanced_stats(df_sessions.to_dict('records'), 'actions'),
            'errors': compute_advanced_stats(df_sessions.to_dict('records'), 'errors')
        }

        # Analyse par utilisateur
        by_user = df_sessions.groupby('user_id').apply(lambda x: {
            'sessions': len(x),
            'duration_stats': compute_advanced_stats(x.to_dict('records'), 'duration'),
            'actions_stats': compute_advanced_stats(x.to_dict('records'), 'actions'),
            'errors_stats': compute_advanced_stats(x.to_dict('records'), 'errors'),
            'domains': list(set.union(*[set(d['domains']) for d in x.to_dict('records')])),
            'features': list(set.union(*[set(d['features']) for d in x.to_dict('records')]))
        }).to_dict()

    return jsonify({
        'session_stats': session_stats,
        'by_user': by_user,
        'total_sessions': len(df_sessions)
    })

# 15. Analyse des erreurs de logs
@app.route('/metrics/log-errors', methods=['GET'])
def get_log_errors():
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.utcnow()) - pd.Timedelta(hours=24)

    errors = redis_client.lrange('errors', 0, -1)
    errors_filtered = [
        e for e in errors
        if pd.to_datetime(e.split(':', 1)[0], errors='coerce') >= cutoff
    ]
    return jsonify({'errors': errors_filtered})

if __name__ == '__main__':
    load_metrics()
    save_thread = threading.Thread(target=periodic_save, daemon=True)
    save_thread.start()
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    redis_thread = threading.Thread(target=listen_redis, daemon=True)
    redis_thread.start()
    app.run(host='0.0.0.0', port=8000,debug=True)