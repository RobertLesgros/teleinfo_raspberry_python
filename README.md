# Teleinfo Raspberry Pi - Compteur Linky vers MQTT

Ce projet permet de lire les données de téléinformation d'un compteur Linky Enedis via un Raspberry Pi et de les publier sur un broker MQTT pour les intégrer à Home Assistant.

## Prérequis matériels

- **Raspberry Pi** (testé sur Pi Zero, Pi 3, Pi 4)
- **Module PITInfo** : [Tindie](https://www.tindie.com/products/Hallard/pitinfo/) ou [Documentation](https://hallard.me/pitinfov12/)
- **Compteur Linky** avec sortie téléinformation (bornes I1/I2)
- Câbles pour relier le PITInfo au compteur

### Schéma de câblage

Le module PITInfo se connecte :
- Au Raspberry Pi via les GPIO (port série)
- Au compteur Linky via les bornes I1 et I2 (sorties téléinformation)

Référence du schéma : [Schéma téléinfo](https://hallard.me/blog/wp-content/uploads/2015/10/schma-final-tlinfo-transistor-fet.jpg)

## Installation sur un Raspberry Pi vierge

### Étape 1 : Installer le système d'exploitation

1. Télécharger [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Flasher une carte SD avec **Raspberry Pi OS Lite (64-bit)** ou **DietPi**
3. Activer SSH avant le premier démarrage :
   ```bash
   # Créer un fichier vide 'ssh' sur la partition boot
   touch /boot/ssh
   ```
4. Démarrer le Raspberry Pi et se connecter en SSH

### Étape 2 : Mettre à jour le système

```bash
sudo apt update && sudo apt upgrade -y
```

### Étape 3 : Activer le port série

Le module PITInfo utilise le port série du Raspberry Pi.

```bash
sudo raspi-config
```

1. Aller dans **Interface Options** > **Serial Port**
2. Répondre **Non** à "Would you like a login shell to be accessible over serial?"
3. Répondre **Oui** à "Would you like the serial port hardware to be enabled?"
4. Redémarrer : `sudo reboot`

Vérifier que le port série est actif :
```bash
ls -la /dev/ttyS0
# ou pour le Pi 3/4 avec Bluetooth désactivé :
ls -la /dev/ttyAMA0
```

### Étape 4 : Installer les dépendances Python

```bash
sudo apt install -y python3 python3-pip python3-serial

# Installer les bibliothèques Python requises
pip3 install pyserial paho-mqtt
```

### Étape 5 : Cloner le projet

```bash
cd ~
git clone https://github.com/RobertLesgros/teleinfo_raspberry_python.git
cd teleinfo_raspberry_python
```

### Étape 6 : Configurer les identifiants MQTT

Copier le fichier d'exemple et le modifier :

```bash
cp credits.py.example credits.py
nano credits.py
```

Renseigner vos informations :
```python
mqtt_username = "votre_utilisateur"
mqtt_password = "votre_mot_de_passe"
mqtt_broker_address = "192.168.1.xxx"  # IP de votre broker MQTT
mqtt_broker_port = 1883
enable_logs = True
```

### Étape 7 : Tester le script manuellement

```bash
python3 teleinfo.py
```

Vous devriez voir "Lancement téléinfo" et les données devraient être publiées sur MQTT.

Appuyez sur `Ctrl+C` pour arrêter.

### Étape 8 : Installer le service systemd

Copier et adapter le fichier de service :

```bash
# Éditer le fichier de service pour votre utilisateur
sudo nano /etc/systemd/system/teleinfo.service
```

Contenu (adapter le chemin et l'utilisateur) :
```ini
[Unit]
Description=Teleinfo compteur Enedis
After=network.target

[Service]
Type=simple
ExecStartPre=/bin/sleep 10
ExecStart=/usr/bin/python3 /home/pi/teleinfo_raspberry_python/teleinfo.py
Restart=always
User=pi
WorkingDirectory=/home/pi/teleinfo_raspberry_python

[Install]
WantedBy=multi-user.target
```

Activer et démarrer le service :

```bash
sudo systemctl daemon-reload
sudo systemctl enable teleinfo.service
sudo systemctl start teleinfo.service
```

Vérifier le statut :
```bash
sudo systemctl status teleinfo.service
```

### Étape 9 : Consulter les logs

```bash
# Logs du service
sudo journalctl -u teleinfo.service -f

# Logs de l'application
tail -f ~/teleinfo_raspberry_python/teleinfo.log
```

## Configuration Home Assistant

Le script publie automatiquement la configuration MQTT Discovery. Les entités suivantes seront créées automatiquement :

| Entité | Description | Unité |
|--------|-------------|-------|
| `PAPP` | Puissance apparente instantanée | W |
| `BBRHCJB` | Index Heures Creuses Jours Bleus | Wh |
| `BBRHPJB` | Index Heures Pleines Jours Bleus | Wh |
| `BBRHCJW` | Index Heures Creuses Jours Blancs | Wh |
| `BBRHPJW` | Index Heures Pleines Jours Blancs | Wh |
| `BBRHCJR` | Index Heures Creuses Jours Rouges | Wh |
| `BBRHPJR` | Index Heures Pleines Jours Rouges | Wh |
| `PTEC` | Période tarifaire en cours | - |
| `DEMAIN` | Couleur du lendemain (Tempo) | - |

## Dépannage

### Le port série ne répond pas

```bash
# Vérifier les permissions
sudo usermod -a -G dialout $USER
# Redémarrer la session
```

### Erreur de connexion MQTT

- Vérifier que le broker MQTT est accessible : `ping <ip_broker>`
- Vérifier les identifiants dans `credits.py`
- Tester la connexion : `mosquitto_pub -h <ip_broker> -u <user> -P <pass> -t test -m "hello"`

### Données invalides ou absentes

- Vérifier le câblage du module PITInfo
- Tester la réception série :
  ```bash
  sudo cat /dev/ttyS0
  ```
  Vous devriez voir des caractères défiler si le compteur envoie des données.

## Références

- [Module PITInfo v1.2](https://hallard.me/pitinfov12/)
- [Tutoriel téléinformation](https://faire-ca-soi-meme.fr/domotique/2016/09/12/module-teleinformation-tic/)
- [Documentation Enedis Téléinformation](https://www.enedis.fr/media/2035/download)

## Licence

Ce projet est distribué sous licence MIT.
