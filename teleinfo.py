#!/usr/bin/python3
# -*- coding: utf-8 -*-

import time
import serial
import paho.mqtt.client as mqtt
import sys
import logging
import signal
import json
import os

# Chemin du script pour les fichiers relatifs
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from credits import mqtt_username, mqtt_password, mqtt_broker_address, mqtt_broker_port, enable_logs

# Configurations
hassDiscoveryPrefix = "homeassistant"
mqttBaseTopic = "teleinfo"

# Configuration du port série (peut être modifié via variable d'environnement)
SERIAL_PORT = os.environ.get('TELEINFO_SERIAL_PORT', '/dev/ttyS0')

# Limites de validation pour PAPP (puissance apparente en VA)
PAPP_MIN = 0
PAPP_MAX = 36000  # 36 kVA max pour un compteur monophasé standard

# Timeout pour la lecture d'une trame complète (en secondes)
TRAME_TIMEOUT = 10

# Configuration des logs
log_file = os.path.join(SCRIPT_DIR, 'teleinfo.log')
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

ser = None
run = True
discovery_published = set()  # Pour ne publier Discovery qu'une fois par clé

# Statistiques de qualité de ligne
stats = {
    "trames_recues": 0,
    "trames_valides": 0,
    "erreurs_checksum": 0,
    "erreurs_decodage": 0,
    "erreurs_timeout": 0,
    "valeurs_hors_bornes": 0
}


def signal_handler(sig, frame):
    """Gestionnaire de signal pour arrêt propre."""
    global run
    print("Arrêt en cours et nettoyage...")
    cleanup()
    run = False


# Dernières valeurs valides pour les index (doivent être croissants)
dernieres_valeurs = {
    "BBRHPJB": 0,
    "BBRHCJB": 0,
    "BBRHPJW": 0,
    "BBRHCJW": 0,
    "BBRHPJR": 0,
    "BBRHCJR": 0
}

# Valeurs valides pour PTEC (période tarifaire)
valeurs_valides_ptec = {"HPJB", "HCJB", "HPJW", "HCJW", "HPJR", "HCJR"}

# Valeurs valides pour DEMAIN (couleur Tempo)
valeurs_valides_demain = {"----", "BLEU", "BLAN", "ROUG"}


def calculer_checksum(etiquette, donnee):
    """
    Calcule le checksum d'une ligne téléinfo.

    Le checksum est calculé en faisant la somme des codes ASCII
    de l'étiquette + espace + donnée, puis en appliquant un masque
    0x3F et en ajoutant 0x20 pour obtenir un caractère imprimable.
    """
    chaine = f"{etiquette} {donnee}"
    somme = sum(ord(c) for c in chaine)
    return chr((somme & 0x3F) + 0x20)


def verifier_checksum(ligne):
    """
    Vérifie le checksum d'une ligne téléinfo.

    Format attendu : "ETIQUETTE DONNEE CHECKSUM" ou "ETIQUETTE DONNEE\tCHECKSUM"
    Retourne (etiquette, donnee) si valide, None sinon.
    """
    # Séparer par espace ou tabulation
    # Le format peut être : ETIQUETTE<SP>DONNEE<SP>CHECKSUM ou ETIQUETTE<SP>DONNEE<HT>CHECKSUM
    parts = ligne.replace('\t', ' ').split(' ')

    if len(parts) < 3:
        return None

    etiquette = parts[0]
    checksum_recu = parts[-1]
    donnee = ' '.join(parts[1:-1])  # Au cas où la donnée contient des espaces

    if len(checksum_recu) != 1:
        return None

    checksum_calcule = calculer_checksum(etiquette, donnee)

    if checksum_recu == checksum_calcule:
        return (etiquette, donnee)
    else:
        if enable_logs:
            logging.debug(f"Checksum invalide pour {etiquette}: reçu '{checksum_recu}', attendu '{checksum_calcule}'")
        return None


def est_valide_ptec(value):
    """Vérifie si la valeur PTEC est valide."""
    return value in valeurs_valides_ptec


def est_valide_demain(value):
    """Vérifie si la valeur DEMAIN est valide."""
    return value in valeurs_valides_demain


def est_valide_index(key, value):
    """
    Vérifie si une valeur d'index est valide.
    Les index doivent être croissants (le compteur ne recule jamais).
    """
    try:
        value = int(value)
        if value >= dernieres_valeurs[key]:
            dernieres_valeurs[key] = value
            return True
        else:
            if enable_logs:
                logging.warning(
                    f"Valeur invalide pour {key}: {value} < précédent {dernieres_valeurs[key]}"
                )
            stats["valeurs_hors_bornes"] += 1
            return False
    except ValueError:
        if enable_logs:
            logging.error(f"Erreur de format pour {key}: {value}")
        stats["erreurs_decodage"] += 1
        return False


def est_valide_papp(value):
    """
    Vérifie si la puissance apparente est dans des bornes raisonnables.
    """
    try:
        papp = int(value)
        if PAPP_MIN <= papp <= PAPP_MAX:
            return True
        else:
            if enable_logs:
                logging.warning(f"PAPP hors bornes: {papp} (attendu {PAPP_MIN}-{PAPP_MAX})")
            stats["valeurs_hors_bornes"] += 1
            return False
    except ValueError:
        if enable_logs:
            logging.error(f"Erreur de format pour PAPP: {value}")
        stats["erreurs_decodage"] += 1
        return False


def lectureTrame(ser):
    """
    Lecture d'une trame téléinfo complète avec gestion des erreurs.

    Une trame commence par STX (0x02) et se termine par ETX (0x03).
    Retourne la trame décodée ou None en cas d'erreur.
    """
    trame = []
    start_time = time.time()

    # Attendre le début de trame (STX = 0x02)
    while True:
        if time.time() - start_time > TRAME_TIMEOUT:
            stats["erreurs_timeout"] += 1
            if enable_logs:
                logging.warning("Timeout en attente du début de trame")
            return None

        try:
            byte = ser.read(1)
            if not byte:  # Timeout sur read
                continue
            char = byte.decode('ascii', errors='replace')
            if char == '\x02':  # STX trouvé
                break
        except (UnicodeDecodeError, serial.SerialException) as e:
            stats["erreurs_decodage"] += 1
            if enable_logs:
                logging.debug(f"Erreur de décodage en attente STX: {e}")
            continue

    # Lire jusqu'à la fin de trame (ETX = 0x03)
    start_time = time.time()
    while True:
        if time.time() - start_time > TRAME_TIMEOUT:
            stats["erreurs_timeout"] += 1
            if enable_logs:
                logging.warning("Timeout en lecture de trame")
            return None

        try:
            byte = ser.read(1)
            if not byte:  # Timeout sur read
                continue
            char = byte.decode('ascii', errors='replace')
            if char == '\x03':  # ETX trouvé
                break
            trame.append(char)
        except (UnicodeDecodeError, serial.SerialException) as e:
            stats["erreurs_decodage"] += 1
            if enable_logs:
                logging.debug(f"Erreur de décodage en lecture: {e}")
            # On continue quand même, le checksum filtrera les erreurs
            trame.append('?')

    stats["trames_recues"] += 1
    return "".join(trame)


def decodeTrame(trame):
    """
    Décode une trame téléinfo avec vérification des checksums.

    Retourne un dictionnaire {etiquette: valeur} pour les lignes valides uniquement.
    """
    if trame is None:
        return {}

    lignes = trame.strip().split('\r\n')
    result = {}
    lignes_valides = 0

    for ligne in lignes:
        ligne = ligne.strip()
        if not ligne:
            continue

        validation = verifier_checksum(ligne)
        if validation:
            etiquette, donnee = validation
            result[etiquette] = donnee
            lignes_valides += 1
        else:
            stats["erreurs_checksum"] += 1

    if lignes_valides > 0:
        stats["trames_valides"] += 1

    return result


def on_disconnect(mqttc, obj, rc):
    """Callback appelé lors de la déconnexion du broker MQTT."""
    print("Connexion MQTT perdue. Reconnexion en cours...")
    if enable_logs:
        logging.warning("Connexion MQTT perdue. Reconnexion en cours...")

    try:
        mqttc.reconnect()
    except ConnectionRefusedError:
        print("La tentative de reconnexion a échoué. Réessayer plus tard.")
        if enable_logs:
            logging.error("La tentative de reconnexion a échoué.")


def cleanup():
    """Ferme proprement les ressources ouvertes."""
    print("Fermeture du port série...")
    if enable_logs:
        logging.info("Fermeture du port série...")
        logging.info(f"Statistiques finales: {json.dumps(stats)}")
    if ser:
        ser.close()
    try:
        mqttc.disconnect()
    except:
        pass


def log_and_publish(topic, value):
    """Publie une valeur sur MQTT et l'enregistre dans les logs."""
    mqttc.publish(topic, value)
    if enable_logs:
        logging.info(f"MQTT: {topic} -> {value}")


def publish_sensor_configuration(mqttc, key, unit_of_measurement, state_class, device_class):
    """
    Publie la configuration Home Assistant Discovery pour un capteur.
    Ne publie qu'une seule fois par clé pour éviter le trafic inutile.
    """
    if key in discovery_published:
        return

    config = {
        "unique_id": f"teleinfo_{key}",
        "name": key,
        "state_topic": f"{mqttBaseTopic}/{key}",
        "platform": "mqtt"
    }

    if device_class:
        config["device_class"] = device_class
    if state_class:
        config["state_class"] = state_class
    if unit_of_measurement:
        config["unit_of_measurement"] = unit_of_measurement

    config_topic = f"{hassDiscoveryPrefix}/sensor/{mqttBaseTopic}/{key}/config"
    mqttc.publish(config_topic, json.dumps(config), retain=True)
    discovery_published.add(key)

    if enable_logs:
        logging.info(f"Discovery publié pour {key}")


def publish_stats(mqttc):
    """Publie les statistiques de qualité de ligne sur MQTT."""
    for stat_name, stat_value in stats.items():
        mqttc.publish(f"{mqttBaseTopic}/stats/{stat_name}", stat_value)


# --- Programme principal ---

print("Lancement téléinfo")
print(f"Port série: {SERIAL_PORT}")

mqttc = mqtt.Client(client_id="teleinfo")
mqttc.username_pw_set(mqtt_username, mqtt_password)
mqttc.on_disconnect = on_disconnect

try:
    ser = serial.Serial(
        port=SERIAL_PORT,
        baudrate=1200,
        bytesize=serial.SEVENBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        timeout=1
    )

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)  # Pour arrêt via systemd

    mqttc.connect(mqtt_broker_address, mqtt_broker_port, 60)
    mqttc.loop_start()

    last_stats_publish = 0
    STATS_INTERVAL = 300  # Publier les stats toutes les 5 minutes

    while run:
        trame = lectureTrame(ser)
        lignes = decodeTrame(trame)

        for key, value in lignes.items():
            # Index de consommation (doivent être croissants)
            if key in dernieres_valeurs:
                if est_valide_index(key, value):
                    log_and_publish(f"{mqttBaseTopic}/{key}", value)
                    publish_sensor_configuration(mqttc, key, "Wh", "total_increasing", "energy")

            # Puissance apparente instantanée
            elif key == "PAPP":
                if est_valide_papp(value):
                    log_and_publish(f"{mqttBaseTopic}/{key}", value)
                    publish_sensor_configuration(mqttc, key, "VA", "measurement", "apparent_power")

            # Intensité instantanée
            elif key == "IINST":
                try:
                    iinst = int(value)
                    if 0 <= iinst <= 90:  # Max 90A pour un compteur standard
                        log_and_publish(f"{mqttBaseTopic}/{key}", value)
                        publish_sensor_configuration(mqttc, key, "A", "measurement", "current")
                except ValueError:
                    pass

            # Couleur du lendemain (Tempo)
            elif key == "DEMAIN":
                if est_valide_demain(value):
                    log_and_publish(f"{mqttBaseTopic}/{key}", value)
                    publish_sensor_configuration(mqttc, key, None, None, None)

            # Période tarifaire en cours
            elif key == "PTEC":
                if est_valide_ptec(value):
                    log_and_publish(f"{mqttBaseTopic}/{key}", value)
                    publish_sensor_configuration(mqttc, key, None, None, None)
                else:
                    if enable_logs:
                        logging.warning(f"Valeur invalide pour PTEC: {value}")

            # Intensité souscrite
            elif key == "ISOUSC":
                try:
                    isousc = int(value)
                    if 0 < isousc <= 90:
                        log_and_publish(f"{mqttBaseTopic}/{key}", value)
                        publish_sensor_configuration(mqttc, key, "A", None, "current")
                except ValueError:
                    pass

            # Avertissement dépassement de puissance
            elif key == "ADPS":
                log_and_publish(f"{mqttBaseTopic}/{key}", value)
                publish_sensor_configuration(mqttc, key, "A", None, None)

        # Publier les statistiques périodiquement
        current_time = time.time()
        if current_time - last_stats_publish > STATS_INTERVAL:
            publish_stats(mqttc)
            last_stats_publish = current_time
            if enable_logs:
                taux_erreur = 0
                if stats["trames_recues"] > 0:
                    taux_erreur = (1 - stats["trames_valides"] / stats["trames_recues"]) * 100
                logging.info(f"Stats: {stats}, Taux erreur: {taux_erreur:.1f}%")

        time.sleep(1)

except KeyboardInterrupt:
    pass
except serial.SerialException as e:
    print(f"Erreur port série: {e}")
    if enable_logs:
        logging.error(f"Erreur port série: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Erreur inattendue: {e}")
    if enable_logs:
        logging.error(f"Erreur inattendue: {e}")
    sys.exit(1)

cleanup()
