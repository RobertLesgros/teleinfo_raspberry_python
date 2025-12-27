#!/usr/bin/python3
# -*- coding: utf-8 -*-

import time
import serial
import paho.mqtt.client as mqtt
import sys
import logging
import signal
import json
from credits import mqtt_username, mqtt_password, mqtt_broker_address, mqtt_broker_port, enable_logs

sys.path.append('/home/dietpi')

# Configurations
hassDiscoveryPrefix = "homeassistant"
mqttBaseTopic = "teleinfo"
logging.basicConfig(filename='/home/dietpi/teleinfo.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

ser = None
run = True

# Définir un gestionnaire de signal pour arrêter le script proprement
def signal_handler(signal, frame):
    global run
    print("Arrêt en cours et nettoyage...")
    cleanup()
    run = False

# Initialiser un dictionnaire pour stocker les dernières valeurs valides
dernieres_valeurs = {
    "BBRHPJB": 0,
    "BBRHCJB": 0,
    "BBRHPJW": 0,
    "BBRHCJW": 0,
    "BBRHPJR": 0,
    "BBRHCJR": 0
}
# Définir les valeurs valides pour PTEC
valeurs_valides_ptec = {"HPJB", "HCJB", "HPJW", "HCJW", "HPJR", "HCJR"}

def est_valide_ptec(value):
    return value in valeurs_valides_ptec

def est_valide(key, value):
    try:
        value = int(value)  # Convertit la valeur en entier
        if value >= dernieres_valeurs[key]:
            dernieres_valeurs[key] = value
            return True
        else:
            if enable_logs:
                logging.warning(f"Valeur invalide pour {key}: {value} est inférieur à la valeur précédente {dernieres_valeurs[key]}")
            return False
    except ValueError:
        if enable_logs:
            logging.error(f"Erreur de format pour {key}: {value}")
        return False

# Lecture et décodage des trames
def lectureTrame(ser):
    trame = list()
    while trame[-2:] != ['\x02', '\n']:
        trame.append(ser.read(1).decode('utf-8'))
    trame = list()
    while trame[-1:] != ['\x03']:
        trame.append(ser.read(1).decode('utf-8'))
    trame.pop()
    trame.pop()
    return "".join(trame)

def decodeTrame(trame):
    lignes = trame.split('\r\n')
    result = {}
    for ligne in lignes:
        items = ligne.split(' ')
        if len(items) >= 2:
            result[items[0]] = items[1]
    return result

# Fonction appelée lors de la déconnexion du broker MQTT
def on_disconnect(mqttc, obj, rc):
    print("Connexion MQTT perdue. Reconnexion en cours...")
    if enable_logs:
        logging.warning("Connexion MQTT perdue. Reconnexion en cours...")

    try:
        mqttc.reconnect()
    except ConnectionRefusedError:
        print("La tentative de reconnexion a échoué. Réessayer plus tard.")
        if enable_logs:
            logging.error("La tentative de reconnexion a échoué. Réessayer plus tard.")

# Fermer les ressources ouvertes
def cleanup():
    print("Fermeture du port série...")
    if enable_logs:
        logging.info("Fermeture du port série...")
    if ser:
        ser.close()
    mqttc.disconnect()

# Publier les données et écrire les logs
def log_and_publish(topic, value):
    mqttc.publish(topic, value)
    if enable_logs:
        logging.info(f"Publié sur MQTT: {topic} -> {value}")

# Publier la configuration du capteur pour Home Assistant
def publish_sensor_configuration(mqttc, key, value, type_info, unit_of_measurement, state_class, device_class):
    # Établir la configuration en fonction des arguments fournis
    config = {
        "unique_id": f"teleinfo_{key}",
        "name": key,
        "state_topic": f"teleinfo/{key}",
        "device_class": device_class,
        "state_class": state_class,
        "unit_of_measurement": unit_of_measurement,
        "platform": "mqtt"
    }

    config_topic = f"{hassDiscoveryPrefix}/sensor/{mqttBaseTopic}/{key}/config"
    mqttc.publish(config_topic, json.dumps(config), retain=True)

print("Lancement téléinfo")

mqttc = mqtt.Client(client_id="teleinfo")
mqttc.username_pw_set(mqtt_username, mqtt_password)
mqttc.on_disconnect = on_disconnect

try:
# [Initialisation du port série et du signal SIGINT...]
    ser = serial.Serial(
        port='/dev/ttyS0',
        baudrate=1200,
        bytesize=serial.SEVENBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        timeout=1
    )

    signal.signal(signal.SIGINT, signal_handler)

    mqttc.connect(mqtt_broker_address, mqtt_broker_port, 60)
    mqttc.loop_start()


    while run:
        trame = lectureTrame(ser)
        lignes = decodeTrame(trame)

        for key, value in lignes.items():
            if key in dernieres_valeurs:
                if est_valide(key, value):
                    log_and_publish(f"teleinfo/{key}", value)
                    # Configuration spécifique pour les clés BBRH
                    if key in ("BBRHCJB", "BBRHCJW", "BBRHCJR", "BBRHPJB", "BBRHPJW", "BBRHPJR"):
                        publish_sensor_configuration(mqttc, key, value, "entier", "Wh", "total_increasing", "energy")
            elif key == "PAPP":
                # Publier directement PAPP sans validation supplémentaire
                log_and_publish(f"teleinfo/{key}", value)
                publish_sensor_configuration(mqttc, key, value, "entier", "W", "measurement", "power")
            elif key == "DEMAIN":
                # Publier directement PAPP sans validation supplémentaire
                log_and_publish(f"teleinfo/{key}", value)
                publish_sensor_configuration(mqttc, key, value, "mot", None, "measurement", None)
            elif key == "PTEC":
                if est_valide_ptec(value):
                    log_and_publish(f"teleinfo/{key}", value)
                    publish_sensor_configuration(mqttc, key, value, "mot", None, "measurement", None)
                else:
                    if enable_logs:
                        logging.warning(f"Valeur invalide pour PTEC: {value}")


        time.sleep(1)


except KeyboardInterrupt:
    pass
except serial.SerialException as e:
    print(f"Erreur lors de l'ouverture du port série : {e}")
    if enable_logs:
        logging.error(f"Erreur lors de l'ouverture du port série : {e}")
    sys.exit(1)

cleanup()
