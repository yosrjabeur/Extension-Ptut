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
                host='172.19.23.119',
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
                if len(parts) < 5:
                    logger.warning(f"Clé mal formée supprimée de redundant_actions : {key}")
                    continue
                try:
                    timestamp = ':'.join(parts[4:])
                    pd.to_datetime(timestamp)
                    cleaned_redundant[key] = value
                except Exception as e:
                    logger.warning(f"Timestamp invalide dans redundant_actions, clé supprimée : {key}, erreur : {e}")
                    continue
            metrics['redundant_actions'] = defaultdict(int, cleaned_redundant)
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
            raw_user_sessions = json.loads(redis_client.get('metrics:user_sessions') or '{}')
            user_sessions = defaultdict(list)
            user_sessions.update(raw_user_sessions)
            metrics['user_sessions'] = user_sessions
            metrics['session_activity'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:session_activity') or '[]'))
            # Normalisation des timestamps dans user_activity
            raw_user_activity = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:user_activity') or '[]'))
            normalized_user_activity = []
            for activity in raw_user_activity:
                ts = ensure_tz_aware(activity['timestamp'])
                if ts is not None:
                    activity['timestamp'] = ts
                    normalized_user_activity.append(activity)
                else:
                    logger.warning(f"Timestamp invalide dans user_activity, activité ignorée : {activity}")
            metrics['user_activity'] = normalized_user_activity
            metrics['error_details'] = convert_strings_to_timestamps(json.loads(redis_client.get('metrics:error_details') or '[]'))
        logger.info("Métriques chargées depuis Redis")
    except Exception as e:
        logger.error(f"Erreur lors du chargement des métriques : {e}")

# Sauvegarder les métriques dans Redis
def convert_strings_to_timestamps(obj):
    if isinstance(obj, str):
        try:
            ts = pd.to_datetime(obj)
            if ts.tzinfo is None:
                return ts.tz_localize('UTC')
            return ts.tz_convert('UTC')
        except (ValueError, TypeError):
            return obj
    elif isinstance(obj, dict):
        return {k: convert_strings_to_timestamps(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_strings_to_timestamps(item) for item in obj]
    elif isinstance(obj, set):
        return {convert_strings_to_timestamps(item) for item in obj}
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
    errors = []

    for field in required_fields:
        if field not in log or log[field] is None or (isinstance(log[field], str) and not log[field].strip()):
            errors.append(f"Champ requis manquant ou vide : {field}")

    try:
        pd.to_datetime(log['timestamp'])
    except Exception as e:
        errors.append(f"Timestamp invalide : {log['timestamp']}, erreur : {e}")

    valid_types = [
        'task_start', 'task_end', 'error', 'cancel', 'click', 'input',
        'help_access', 'tutorial_access', 'shortcut', 'alert_shown', 'alert_response',
        'network', 'network-error', 'page_engagement', 'redundant', 'form-error', 'validation-error'
    ]
    if log['type'] not in valid_types:
        errors.append(f"Type de log invalide : {log['type']}")

    if log['type'] == 'page_engagement':
        try:
            float(log['details'].get('active_time', '0s').replace('s', ''))
            float(log['details'].get('max_scroll_depth', '0%').replace('%', ''))
        except (ValueError, TypeError) as e:
            errors.append(f"Valeurs invalides dans page_engagement : {e}")

    if log['type'] in ['task_start', 'task_end']:
        if 'task_id' not in log or not log['task_id']:
            errors.append(f"task_id manquant pour le log de type {log['type']}")
        if 'details' not in log or not isinstance(log['details'], dict):
            errors.append(f"details manquant ou invalide pour le log de type {log['type']}")
        else:
            if 'name' not in log['details'] or not log['details']['name']:
                errors.append(f"Nom de la tâche manquant dans details pour le log de type {log['type']}")

    if errors:
        error_message = "Erreurs détectées dans le log : " + "; ".join(errors)
        logger.error(f"{error_message}\nLog: {json.dumps(log)}")
        redis_client.lpush('errors', f"{error_message}: {json.dumps(log)}")
        raise ValueError(error_message)

    return True

# Nettoyage des données
def cleanup_metrics():
    current_time = pd.to_datetime(datetime.now(timezone.utc))
    cutoff_24h = current_time - pd.Timedelta(hours=24)
    cutoff_5min = current_time - pd.Timedelta(minutes=5)

    with metrics_lock:
        # Détection des tâches non terminées (timeout de 15 minutes)
        for task in metrics['task_times']:
            if 'end' not in task and not task['is_cancelled']:
                task_start = ensure_tz_aware(task['start'])
                if task_start and (current_time - task_start).total_seconds() >= 900:  # 15 minutes
                    task['is_cancelled'] = True
                    task['end'] = current_time
                    task['duration'] = (task['end'] - task['start']).total_seconds()
                    logger.info(f"Tâche non terminée (timeout) : {task}")
                    redis_client.lpush('cancellations', f"Tâche non terminée (timeout) : task_id={task['task_id']}, user_id={task['user_id']}, domain={task['domain']}")

        metrics['task_times'] = [
            task for task in metrics['task_times']
            if ('duration' not in task and (current_time - ensure_tz_aware(task['start'])).total_seconds() < 900)
            or ('duration' in task and ensure_tz_aware(task['start']) > cutoff_24h)
        ]
        metrics['alert_times'] = [
            alert for alert in metrics['alert_times']
            if ('response_time' not in alert and (current_time - ensure_tz_aware(alert['start'])).total_seconds() < 900)
            or ('response_time' in alert and ensure_tz_aware(alert['start']) > cutoff_24h)
        ]
        metrics['network_requests'] = [
            req for req in metrics['network_requests']
            if ensure_tz_aware(req['timestamp']) > cutoff_24h
        ]
        metrics['page_engagement'] = [
            eng for eng in metrics['page_engagement']
            if ensure_tz_aware(eng['timestamp']) > cutoff_24h
        ]
        metrics['session_activity'] = [
            session for session in metrics['session_activity']
            if ensure_tz_aware(session['start']) > cutoff_24h
        ]
        metrics['user_activity'] = [
            activity for activity in metrics['user_activity']
            if ensure_tz_aware(activity['timestamp']) > cutoff_24h
        ]
        metrics['error_details'] = [
            error for error in metrics['error_details']
            if ensure_tz_aware(error['timestamp']) > cutoff_24h
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

        task_name = details.get('name', 'unknown')
        if task_name.startswith('http://') or task_name.startswith('https://'):
            logger.warning(f"Task name appears to be a URL: {task_name}. Attempting to infer task type.")
            if log_type in ['task_start', 'task_end']:
                if 'form' in log['url'].lower():
                    task_name = 'form_submit'
                else:
                    task_name = 'unknown_task'
            else:
                task_name = 'unknown'
        details['name'] = task_name

        timestamp = ensure_tz_aware(log['timestamp'])
        if timestamp is None:
            raise ValueError(f"Timestamp invalide dans le log : {log['timestamp']}")
        log['timestamp'] = timestamp

        with metrics_lock:
            metrics['total_actions'] += 1
            metrics['by_domain'][domain]['total_actions'] += 1

            # Ajout à user_activity pour toutes les actions
            activity = {
                'user_id': user_id,
                'session_id': session_id,
                'timestamp': timestamp,
                'type': log_type,
                'domain': domain,
                'feature': details.get('feature', 'N/A'),
                'details': details
            }
            metrics['user_activity'].append(activity)
            logger.debug(f"Activité ajoutée à user_activity : {activity}")

            # Gestion des sessions
            if session_id not in metrics['user_sessions'][user_id]:
                metrics['user_sessions'][user_id].append(session_id)
                metrics['session_activity'].append({
                    'user_id': user_id,
                    'session_id': session_id,
                    'start': timestamp,
                    'actions': 0,
                    'domains': [],
                    'features': [],
                    'errors': 0
                })
            for session in metrics['session_activity']:
                if session['user_id'] == user_id and session['session_id'] == session_id:
                    session['actions'] += 1
                    session['domains'].append(domain)
                    feature = details.get('feature', 'N/A')
                    if feature not in session['features']:
                        session['features'].append(feature)
                    if log_type in ['error', 'network-error', 'form-error', 'validation-error', 'alert-error', 'input-error', 'input-correction', 'task-failure', 'submit-failure']:
                        session['errors'] += 1
                    if 'end' not in session:
                        session['duration'] = (timestamp - session['start']).total_seconds()

            # Gestion des logs de type "shortcut"
            if log_type == 'shortcut':
                combination = details.get('combination')
                if not combination:
                    logger.warning(f"Log de type 'shortcut' sans 'combination' dans details : {log}")
                    details['combination'] = 'Unknown'

            # Gestion des logs de type "redundant"
            if log_type == 'redundant':
                action_type = details.get('action_type', 'unknown')
                count = details.get('count', 1)
                if not isinstance(count, int) or count < 0:
                    logger.warning(f"Valeur de count invalide dans log de type redundant : {count}, utilisation de la valeur par défaut 1")
                    count = 1
                redundant_key = f"{user_id}:{session_id}:{action_type}:{details.get('action_detail', 'unknown')}:{timestamp.isoformat()}"
                metrics['redundant_actions'][redundant_key] = metrics['redundant_actions'].get(redundant_key, 0) + count
                logger.debug(f"Action redondante enregistrée : {redundant_key} -> {count}")

            # Gestion des logs de type "alert_response"
            if log_type == 'alert_response':
                start_time = details.get('start_time')
                if start_time:
                    start_time = ensure_tz_aware(start_time)
                    response_time = (timestamp - start_time).total_seconds()
                    metrics['alert_times'].append({
                        'start': start_time,
                        'end': timestamp,
                        'response_time': response_time,
                        'message': details.get('message', 'N/A'),
                        'domain': domain
                    })

            # Détection des actions redondantes pour les clics et les saisies
            if log_type in ['click', 'input']:
                action_key = f"{log_type}:{details.get('element', 'unknown')}:{details.get('value', 'unknown')}"
                recent_actions = [
                    a for a in metrics['user_activity']
                    if a['user_id'] == user_id
                    and a['session_id'] == session_id
                    and a['type'] == log_type
                    and f"{a['type']}:{a['details'].get('element', 'unknown')}:{a['details'].get('value', 'unknown')}" == action_key
                    and (timestamp - ensure_tz_aware(a['timestamp'])).total_seconds() <= 5
                ]
                if recent_actions:
                    redundant_key = f"{user_id}:{session_id}:{log_type}:{details.get('element', 'unknown')}:{timestamp.isoformat()}"
                    metrics['redundant_actions'][redundant_key] = metrics['redundant_actions'].get(redundant_key, 0) + 1
                    logger.debug(f"Action redondante détectée : {redundant_key} -> {metrics['redundant_actions'][redundant_key]}")

            # Gestion des tâches
            task_id = log.get('task_id')
            if task_id:
                if log_type == 'task_start':
                    metrics['tasks_started'] += 1
                    task_entry = {
                        'task_id': task_id,
                        'user_id': user_id,
                        'session_id': session_id,
                        'domain': domain,
                        'name': task_name,
                        'start': timestamp,
                        'is_completed': False,
                        'is_cancelled': False,
                        'form_id': details.get('form_id', 'unknown'),
                        'last_activity': timestamp
                    }
                    metrics['task_times'].append(task_entry)
                    logger.debug(f"Tâche démarrée : {task_entry}")
                elif log_type == 'task_end':
                    for task in metrics['task_times']:
                        if task['task_id'] == task_id and not task.get('is_completed_processed', False):
                            task['is_completed'] = details.get('is_task_completed', False)
                            task['end'] = timestamp
                            task['duration'] = (task['end'] - task['start']).total_seconds()
                            task['name'] = task_name
                            task['reason'] = details.get('reason', None)
                            task['is_completed_processed'] = True
                            if task['is_completed']:
                                task['is_cancelled'] = False
                                metrics['tasks_completed'] += 1
                            else:
                                error_message = f"Échec de la tâche: {task_name}"
                                if task['reason']:
                                    error_message += f", raison: {task['reason']}"
                                error_entry = {
                                    'timestamp': timestamp,
                                    'type': 'task-failure',
                                    'message': error_message,
                                    'domain': domain,
                                    'user_id': user_id,
                                    'session_id': session_id,
                                    'details': {'task_name': task_name, 'reason': task['reason']},
                                    'error_key': f"{user_id}:{session_id}:task-failure:{task_id}"
                                }
                                if not any(e.get('error_key') == error_entry['error_key'] for e in metrics['error_details']):
                                    metrics['error_details'].append(error_entry)
                                    redis_client.lpush('errors_popup', f"Échec de tâche détecté : {error_message}, user_id={user_id}, domain={domain}")
                                    logger.info(f"Échec de tâche enregistré dans metrics['error_details'] : {error_entry}")
                                    logger.debug(f"Total des erreurs après ajout : {len(metrics['error_details'])}")
                            logger.debug(f"Tâche terminée : {task}")
                            break
                elif log_type == 'cancel':
                    for task in metrics['task_times']:
                        if task['task_id'] == task_id and not task['is_completed']:
                            task['is_cancelled'] = True
                            task['end'] = timestamp
                            task['duration'] = (task['end'] - task['start']).total_seconds()
                            logger.info(f"Tâche annulée : {task}")
                            redis_client.lpush('cancellations', f"Tâche annulée : task_id={task_id}, user_id={user_id}, domain={domain}, form_id={details.get('form_id', 'unknown')}")
                            break
                else:
                    for task in metrics['task_times']:
                        if task['session_id'] == session_id and not task['is_completed']:
                            task['last_activity'] = timestamp

            # Gestion des erreurs explicites (error, form-error, validation-error, network-error)
            if log_type in ['error', 'network-error', 'form-error', 'validation-error']:
                error_message = details.get('message', 'Erreur inconnue')
                error_entry = {
                    'timestamp': timestamp,
                    'type': log_type,
                    'message': error_message,
                    'domain': domain,
                    'user_id': user_id,
                    'session_id': session_id,
                    'details': details,
                    'error_key': f"{user_id}:{session_id}:{log_type}:{timestamp.isoformat()}"
                }
                if not any(e.get('error_key') == error_entry['error_key'] for e in metrics['error_details']):
                    metrics['error_details'].append(error_entry)
                    redis_client.lpush('errors_popup', f"Erreur détectée : {error_message}, user_id={user_id}, domain={domain}")
                    logger.info(f"Erreur explicite enregistrée dans metrics['error_details'] : {error_entry}")
                    logger.debug(f"Total des erreurs après ajout : {len(metrics['error_details'])}")

            # Détection des messages d'erreur affichés à l'utilisateur via alert_shown
            if log_type == 'alert_shown':
                message = details.get('message', '').lower()
                error_keywords = ['error', 'erreur', 'invalid', 'invalide', 'failed', 'échoué', 'fail', 'échec', 'required', 'requis']
                if any(keyword in message for keyword in error_keywords):
                    error_entry = {
                        'timestamp': timestamp,
                        'type': 'alert-error',
                        'message': f"Message d'erreur affiché: {message}",
                        'domain': domain,
                        'user_id': user_id,
                        'session_id': session_id,
                        'details': details,
                        'error_key': f"{user_id}:{session_id}:alert-error:{timestamp.isoformat()}"
                    }
                    if not any(e.get('error_key') == error_entry['error_key'] for e in metrics['error_details']):
                        metrics['error_details'].append(error_entry)
                        redis_client.lpush('errors_popup', f"Message d'erreur affiché : {message}, user_id={user_id}, domain={domain}")
                        logger.info(f"Erreur d'alerte enregistrée dans metrics['error_details'] : {error_entry}")
                        logger.debug(f"Total des erreurs après ajout : {len(metrics['error_details'])}")

            # Détection des erreurs de saisie via les logs 'input'
            if log_type == 'input':
                input_field = details.get('element', 'unknown')
                recent_activities = [
                    a for a in metrics['user_activity']
                    if a['user_id'] == user_id
                    and a['session_id'] == session_id
                    and (timestamp - ensure_tz_aware(a['timestamp'])).total_seconds() <= 5  # Réduit à 5 secondes
                ]
                has_error_alert = False
                for activity in recent_activities:
                    # Vérifier si un message d'erreur est lié à cet input
                    if activity['type'] == 'alert_shown':
                        message = activity['details'].get('message', '').lower()
                        error_keywords = ['error', 'invalid', 'required', 'erreur', 'invalide', 'requis']
                        alert_field = activity['details'].get('field', '').lower()  # Vérifier si l'alerte spécifie un champ
                        if any(keyword in message for keyword in error_keywords) and (not alert_field or alert_field == input_field.lower()):
                            error_entry = {
                                'timestamp': timestamp,
                                'type': 'input-error',
                                'message': f"Erreur de saisie détectée: {activity['details'].get('message', 'Champ invalide')}",
                                'domain': domain,
                                'user_id': user_id,
                                'session_id': session_id,
                                'details': {'input_value': details.get('value', ''), 'field': input_field},
                                'error_key': f"{user_id}:{session_id}:input-error:{input_field}:{timestamp.isoformat()}"
                            }
                            if not any(e.get('error_key') == error_entry['error_key'] for e in metrics['error_details']):
                                metrics['error_details'].append(error_entry)
                                redis_client.lpush('errors_popup', f"Erreur de saisie détectée : {error_entry['message']}, user_id={user_id}, domain={domain}")
                                logger.info(f"Erreur de saisie enregistrée dans metrics['error_details'] : {error_entry}")
                                logger.debug(f"Total des erreurs après ajout : {len(metrics['error_details'])}")
                            has_error_alert = True
                            break
                # Détection des corrections rapides uniquement si une erreur a été détectée
                if has_error_alert:
                    for activity in recent_activities:
                        if activity['type'] == 'input' and activity['details'].get('element') == input_field and activity['details'].get('value') != details.get('value'):
                            error_entry = {
                                'timestamp': timestamp,
                                'type': 'input-correction',
                                'message': "Correction rapide détectée après une saisie erronée",
                                'domain': domain,
                                'user_id': user_id,
                                'session_id': session_id,
                                'details': {
                                    'original_value': details.get('value', ''),
                                    'corrected_value': activity['details'].get('value', ''),
                                    'field': input_field
                                },
                                'error_key': f"{user_id}:{session_id}:input-correction:{input_field}:{timestamp.isoformat()}"
                            }
                            if not any(e.get('error_key') == error_entry['error_key'] for e in metrics['error_details']):
                                metrics['error_details'].append(error_entry)
                                redis_client.lpush('errors_popup', f"Correction rapide détectée : {error_entry['message']}, user_id={user_id}, domain={domain}")
                                logger.info(f"Correction de saisie enregistrée dans metrics['error_details'] : {error_entry}")
                                logger.debug(f"Total des erreurs après ajout : {len(metrics['error_details'])}")
                            break

            # Détection des clics répétés sur "submit"
            if log_type == 'click' and details.get('element', '').lower() in ['submit', 'button-submit', 'btn-submit']:
                recent_actions = [
                    a for a in metrics['user_activity']
                    if a['user_id'] == user_id
                    and a['session_id'] == session_id
                    and a['type'] == 'click'
                    and a['details'].get('element', '').lower() in ['submit', 'button-submit', 'btn-submit']
                    and (timestamp - ensure_tz_aware(a['timestamp'])).total_seconds() <= 5
                ]
                if len(recent_actions) > 1:
                    error_message = "Clics répétés sur le bouton de soumission (échec potentiel de soumission)"
                    error_entry = {
                        'timestamp': timestamp,
                        'type': 'submit-failure',
                        'message': error_message,
                        'domain': domain,
                        'user_id': user_id,
                        'session_id': session_id,
                        'details': {'click_count': len(recent_actions), 'element': 'submit'},
                        'error_key': f"{user_id}:{session_id}:submit-failure:{timestamp.isoformat()}"
                    }
                    if not any(e.get('error_key') == error_entry['error_key'] for e in metrics['error_details']):
                        metrics['error_details'].append(error_entry)
                        redis_client.lpush('errors_popup', f"Erreur détectée : {error_message}, user_id={user_id}, domain={domain}")
                        logger.info(f"Erreur de soumission répétée enregistrée dans metrics['error_details'] : {error_entry}")
                        logger.debug(f"Total des erreurs après ajout : {len(metrics['error_details'])}")

        save_metrics_transactional()

    except Exception as e:
        error_message = f"Erreur traitement log : {e}\nTrace: {traceback.format_exc()}\nLog: {log}"
        logger.error(error_message)
        redis_client.lpush('errors_popup', error_message)
        redis_client.lpush('errors', error_message)

# Écoute Redis
def listen_redis():
    logger.info("Démarrage de l'écoute Redis sur le canal logs:realtime")
    for message in pubsub.listen():
        if message['type'] == 'message':
            try:
                log = json.loads(message['data'])
                logger.info(f"Log brut reçu via Redis : {log}")
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

def ensure_tz_aware(timestamp):
    try:
        ts = pd.to_datetime(timestamp)
        if ts.tzinfo is None:
            return ts.tz_localize('UTC')
        return ts.tz_convert('UTC')
    except (ValueError, TypeError) as e:
        logger.warning(f"Timestamp invalide : {timestamp}, erreur : {e}")
        return None
    
# 1. Analyse des temps d'exécution des tâches
@app.route('/metrics/task-stats', methods=['GET'])
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
@app.route('/metrics/errors', methods=['GET'])
def get_errors_and_cancellations():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')

    try:
        current_time = pd.to_datetime(datetime.now(timezone.utc))
        if time_window.endswith('h'):
            cutoff = current_time - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = current_time - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = current_time - pd.Timedelta(hours=24)
        logger.info(f"Appel à /metrics/errors - time_window: {time_window}, domain_filter: {domain_filter}, cutoff: {cutoff}")
    except ValueError as e:
        logger.error(f"Erreur lors du parsing de time_window '{time_window}': {e}")
        cutoff = current_time - pd.Timedelta(hours=24)

    try:
        with metrics_lock:
            # Vérifier le contenu brut de metrics['error_details'] avant filtrage
            logger.debug(f"Contenu brut de metrics['error_details']: {metrics['error_details']}")

            # Filtrer les erreurs
            errors_filtered = []
            for e in metrics['error_details']:
                ts = ensure_tz_aware(e['timestamp'])
                if ts is None:
                    logger.warning(f"Timestamp invalide dans error_details: {e}")
                    continue
                if ts >= cutoff and (domain_filter is None or e['domain'] == domain_filter):
                    errors_filtered.append(e)
                else:
                    logger.debug(f"Erreur ignorée - timestamp: {ts}, domain: {e['domain']}, cutoff: {cutoff}, domain_filter: {domain_filter}")

            logger.debug(f"Erreurs filtrées: {len(errors_filtered)}, détails: {errors_filtered}")

            # Filtrer les activités utilisateur
            user_activity_filtered = [
                a for a in metrics['user_activity']
                if (ts := ensure_tz_aware(a['timestamp'])) is not None and ts >= cutoff
                and (domain_filter is None or a['domain'] == domain_filter)
            ]
            total_actions_filtered = len(user_activity_filtered) or 1
            logger.debug(f"Actions filtrées: {total_actions_filtered}")

            # Calculer les métriques globales
            error_rate = (len(errors_filtered) / total_actions_filtered * 100)
            cancellations_filtered = sum(
                1 for task in metrics['task_times']
                if task['is_cancelled'] and not task['is_completed']
                and (ts := ensure_tz_aware(task['end'])) is not None and ts >= cutoff
                and (domain_filter is None or task['domain'] == domain_filter)
            )
            cancellation_rate = (cancellations_filtered / total_actions_filtered * 100)

            # Analyse des types et messages d'erreurs
            df_errors = pd.DataFrame(errors_filtered)
            error_types = df_errors.groupby('type').size().to_dict() if not df_errors.empty else {}
            error_messages = df_errors.groupby('message').size().to_dict() if not df_errors.empty else {}

            # Analyse des erreurs de formulaire (inclure les nouveaux types)
            form_errors = [
                e for e in errors_filtered
                if e['type'] in ['form-error', 'validation-error', 'task-failure', 'submit-failure', 'input-error', 'input-correction', 'alert-error']
            ]
            form_error_count = len(form_errors)
            form_error_rate = (form_error_count / total_actions_filtered * 100) if total_actions_filtered > 0 else 0
            form_error_messages = pd.DataFrame(form_errors).groupby('message').size().to_dict() if form_errors else {}

            # Analyse par domaine
            by_domain = {}
            domains = {domain_filter} if domain_filter else set(metrics['by_domain'].keys())
            for domain in domains:
                domain_errors = [
                    e for e in errors_filtered if e['domain'] == domain
                ]
                domain_form_errors = [
                    e for e in form_errors if e['domain'] == domain
                ]
                domain_actions = [
                    a for a in user_activity_filtered if a['domain'] == domain
                ]
                domain_action_count = len(domain_actions) or 1
                domain_cancellations = sum(
                    1 for task in metrics['task_times']
                    if task['domain'] == domain and task['is_cancelled'] and not task['is_completed']
                    and (ts := ensure_tz_aware(task['end'])) is not None and ts >= cutoff
                )
                by_domain[domain] = {
                    'errors': len(domain_errors),
                    'error_rate': round((len(domain_errors) / domain_action_count * 100), 2),
                    'form_errors': len(domain_form_errors),
                    'form_error_rate': round((len(domain_form_errors) / domain_action_count * 100), 2),
                    'cancellations': domain_cancellations,
                    'cancellation_rate': round((domain_cancellations / domain_action_count * 100), 2),
                    'total_actions': domain_action_count
                }

            # Préparer les détails des erreurs pour la réponse
            error_details = [
                {
                    'timestamp': e['timestamp'].isoformat(),
                    'type': e['type'],
                    'message': e['message'],
                    'domain': e['domain'],
                    'user_id': e['user_id'],
                    'session_id': e['session_id'],
                    'details': e.get('details', {})
                } for e in errors_filtered
            ]

        logger.info(f"Retour des métriques - erreurs: {len(errors_filtered)}, types: {error_types}")
        return jsonify({
            'errors': len(errors_filtered),
            'error_rate': round(error_rate, 2),
            'error_types': error_types,
            'error_messages': error_messages,
            'form_errors': form_error_count,
            'form_error_rate': round(form_error_rate, 2),
            'form_error_messages': form_error_messages,
            'cancellations': cancellations_filtered,
            'cancellation_rate': round(cancellation_rate, 2),
            'total_actions': total_actions_filtered,
            'by_domain': by_domain,
            'error_details': error_details
        })

    except Exception as e:
        logger.error(f"Erreur dans get_errors_and_cancellations : {str(e)}\nTrace: {traceback.format_exc()}")
        return jsonify({'error': f'Erreur serveur: {str(e)}'}), 500 
     
# 3. Analyse des actions redondantes
@app.route('/metrics/redundant-actions', methods=['GET'])
def get_redundant_actions():
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')

    try:
        current_time = pd.to_datetime(datetime.now(timezone.utc))
        if time_window.endswith('h'):
            cutoff = current_time - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = current_time - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = current_time - pd.Timedelta(hours=24)
    except ValueError as e:
        logger.error(f"Erreur lors du parsing de time_window '{time_window}': {e}")
        cutoff = current_time - pd.Timedelta(hours=24)

    try:
        with metrics_lock:
            user_activity_filtered = [
                a for a in metrics['user_activity']
                if (ts := ensure_tz_aware(a['timestamp'])) is not None and ts >= cutoff
                and (domain_filter is None or a['domain'] == domain_filter)
            ]
            total_actions_filtered = len(user_activity_filtered) or 1
            logger.debug(f"Total actions filtered: {total_actions_filtered}, domain_filter: {domain_filter}, cutoff: {cutoff}")

            domain_action_counts = defaultdict(int)
            for activity in user_activity_filtered:
                domain_action_counts[activity['domain']] += 1
            for domain in domain_action_counts:
                domain_action_counts[domain] = domain_action_counts[domain] or 1

            total_redundant = 0
            redundant_by_type = defaultdict(int)
            by_domain = defaultdict(lambda: {'total_redundant': 0, 'redundant_rate': 0.0})

            # Créer un index des activités par user_id et session_id
            activity_index = defaultdict(list)
            for activity in metrics['user_activity']:
                key = (activity['user_id'], activity['session_id'])
                activity_index[key].append(activity)

            logger.debug(f"Contenu de metrics['redundant_actions']: {dict(metrics['redundant_actions'])}")

            for key, count in metrics['redundant_actions'].items():
                parts = key.split(':')
                if len(parts) < 5:
                    logger.warning(f"Clé mal formée dans redundant_actions : {key}")
                    continue
                user_id, session_id, action_type, action_detail, timestamp = parts[0], parts[1], parts[2], parts[3], ':'.join(parts[4:])
                try:
                    action_time = ensure_tz_aware(timestamp)
                    if action_time is None:
                        logger.warning(f"Timestamp invalide pour la clé dans redundant_actions : {key}")
                        continue
                except Exception as e:
                    logger.error(f"Erreur lors du parsing du timestamp pour la clé {key} dans redundant_actions : {timestamp}, erreur : {e}")
                    continue

                domain = None
                # Rechercher le domaine dans toutes les activités de la session
                for activity in activity_index.get((user_id, session_id), []):
                    activity_time = ensure_tz_aware(activity['timestamp'])
                    time_diff = abs((activity_time - action_time).total_seconds())
                    if time_diff < 1:  # Tolérance de 1 seconde
                        domain = activity['domain']
                        break
                if domain is None:
                    logger.warning(f"Domaine non trouvé pour la clé : {key}")
                    continue

                if (domain_filter is None or domain == domain_filter) and action_time >= cutoff:
                    total_redundant += count
                    redundant_by_type[action_type] += count
                    by_domain[domain]['total_redundant'] += count
                else:
                    logger.debug(f"Action redondante ignorée - domaine: {domain}, action_time: {action_time}, domain_filter: {domain_filter}, cutoff: {cutoff}")

            for domain in by_domain:
                domain_actions = domain_action_counts.get(domain, 1)
                by_domain[domain]['redundant_rate'] = round(
                    (by_domain[domain]['total_redundant'] / domain_actions * 100), 2
                )

            redundant_rate = (total_redundant / total_actions_filtered * 100)

            return jsonify({
                'redundant_actions': dict(redundant_by_type),
                'total_redundant': total_redundant,
                'redundant_rate': round(redundant_rate, 2),
                'total_actions': total_actions_filtered,
                'by_domain': dict(by_domain)
            })

    except Exception as e:
        logger.error(f"Erreur dans get_redundant_actions : {str(e)}")
        return jsonify({'error': 'Erreur serveur lors du calcul des métriques.'}), 500
    
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
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=24)

    with metrics_lock:
        tasks_started = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'task_start' and (ts := ensure_tz_aware(a['timestamp'])) is not None and ts >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
            or (ts is None and logger.warning(f"Timestamp invalide dans user_activity : {a['timestamp']}"))
        )
        tasks_completed = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'task_end' and a['details'].get('is_task_completed', False)
            and (ts := ensure_tz_aware(a['timestamp'])) is not None and ts >= cutoff
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
                if a['type'] == 'task_start' and a['domain'] == domain
                and (ts := ensure_tz_aware(a['timestamp'])) is not None and ts >= cutoff
            )
            domain_completed = sum(
                1 for a in metrics['user_activity']
                if a['type'] == 'task_end' and a['details'].get('is_task_completed', False)
                and a['domain'] == domain
                and (ts := ensure_tz_aware(a['timestamp'])) is not None and ts >= cutoff
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
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

    logger.debug(f"Cutoff calculé : {cutoff}")
    logger.debug(f"Domain filter : {domain_filter}")

    with metrics_lock:
        logger.debug(f"Contenu de metrics['user_activity'] : {metrics['user_activity']}")

        # Calculer le total des actions filtrées
        total_actions_filtered = sum(
            1 for a in metrics['user_activity']
            if ensure_tz_aware(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ) or 1
        logger.debug(f"Total actions filtered : {total_actions_filtered}")

        # Calculer l'utilisation des raccourcis
        shortcut_usage = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'shortcut' and ensure_tz_aware(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        )
        shortcut_rate = (shortcut_usage / total_actions_filtered * 100)
        logger.debug(f"Shortcut usage : {shortcut_usage}, Shortcut rate : {shortcut_rate}")

        # Créer le DataFrame pour les raccourcis
        df_shortcuts = pd.DataFrame([
            a for a in metrics['user_activity']
            if a['type'] == 'shortcut' and ensure_tz_aware(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ])
        logger.debug(f"Données filtrées (df_shortcuts) : {df_shortcuts.to_dict('records') if not df_shortcuts.empty else 'Vide'}")

        # Analyse par combinaison de raccourcis
        shortcut_combinations = {}
        if not df_shortcuts.empty:
            # Extraire la clé 'combination' depuis 'details'
            df_shortcuts['combination'] = df_shortcuts['details'].apply(
                lambda x: x.get('combination', 'Unknown') if isinstance(x, dict) else 'Unknown'
            )
            # Log si des raccourcis n'ont pas de 'combination'
            missing_combinations = df_shortcuts[df_shortcuts['combination'] == 'Unknown']
            if not missing_combinations.empty:
                logger.warning(f"Raccourcis sans 'combination' : {missing_combinations.to_dict('records')}")
            shortcut_combinations = df_shortcuts.groupby('combination').size().to_dict()

        # Analyse par domaine
        by_domain = {}
        for domain in metrics['by_domain']:
            if domain_filter and domain != domain_filter:
                continue
            domain_actions = sum(
                1 for a in metrics['user_activity']
                if a['domain'] == domain and ensure_tz_aware(a['timestamp']) >= cutoff
            ) or 1
            domain_shortcuts = sum(
                1 for a in metrics['user_activity']
                if a['type'] == 'shortcut' and a['domain'] == domain and ensure_tz_aware(a['timestamp']) >= cutoff
            )
            by_domain[domain] = {
                'shortcut_usage': domain_shortcuts,
                'shortcut_usage_rate': round((domain_shortcuts / domain_actions * 100), 2)
            }
        logger.debug(f"Statistiques par domaine (by_domain) : {by_domain}")

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
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=24)

    logger.debug("Cutoff calculé : %s", cutoff)
    logger.debug("Domain filter : %s", domain_filter)

    with metrics_lock:
        logger.debug("Contenu de metrics['alert_times'] : %s", metrics['alert_times'])
        df_alerts = pd.DataFrame([
            a for a in metrics['alert_times']
            if 'response_time' in a and ensure_tz_aware(a['start']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        ])
        logger.debug("Données filtrées (df_alerts) : %s", df_alerts.to_dict('records') if not df_alerts.empty else "Vide")

        stats = compute_advanced_stats(df_alerts.to_dict('records'), 'response_time') if not df_alerts.empty else {}
        logger.debug("Statistiques globales (stats) : %s", stats)

        # Analyse par message
        by_message = df_alerts.groupby('message').apply(
            lambda x: compute_advanced_stats(x.to_dict('records'), 'response_time')
        ).to_dict() if not df_alerts.empty else {}
        logger.debug("Statistiques par message (by_message) : %s", by_message)

        # Analyse par domaine
        by_domain = df_alerts.groupby('domain').apply(
            lambda x: compute_advanced_stats(x.to_dict('records'), 'response_time')
        ).to_dict() if not df_alerts.empty else {}
        logger.debug("Statistiques par domaine (by_domain) : %s", by_domain)

        # Taux de réponse
        logger.debug("Contenu de metrics['user_activity'] : %s", metrics['user_activity'])
        alerts_shown = sum(
            1 for a in metrics['user_activity']
            if a['type'] == 'alert_shown' and ensure_tz_aware(a['timestamp']) >= cutoff
            and (domain_filter is None or a['domain'] == domain_filter)
        )
        logger.debug("Nombre d'alertes affichées (alerts_shown) : %s", alerts_shown)
        response_rate = (len(df_alerts) / alerts_shown * 100) if alerts_shown > 0 else 0
        logger.debug("Taux de réponse (response_rate) : %s", response_rate)

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
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc), utc=True) - pd.Timedelta(hours=24)

    with metrics_lock:
        df_activity = pd.DataFrame([
            a for a in metrics['user_activity']
            if a['feature'] != 'N/A' and (ts := ensure_tz_aware(a['timestamp'])) is not None and ts >= cutoff
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
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

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
                'elapsed': (pd.to_datetime(datetime.now(timezone.utc)) - t['start']).total_seconds(),
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
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

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
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

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
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

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
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

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
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

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
    domain_filter = request.args.get('domain')
    time_window = request.args.get('time_window', '24h')
    try:
        if time_window.endswith('h'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=int(time_window[:-1]))
        elif time_window.endswith('d'):
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(days=int(time_window[:-1]))
        else:
            cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)
    except ValueError:
        cutoff = pd.to_datetime(datetime.now(timezone.utc)) - pd.Timedelta(hours=24)

    errors_filtered = [
    e for e in metrics['error_details']
    if (ts := ensure_tz_aware(e['timestamp'])) is not None and ts >= cutoff
    and (domain_filter is None or e['domain'] == domain_filter)
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