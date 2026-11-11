"""Paramètres de l'application. Aucun secret n'est écrit en dur : tout vient
des variables d'environnement, avec des valeurs par défaut sûres."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    # Clé de signature des sessions. Sans variable d'environnement, une clé
    # aléatoire est générée à chaque démarrage (les sessions sont alors perdues
    # au redémarrage, ce qui est acceptable pour un outil local).
    SECRET_KEY = os.environ.get("REVUE_AD_SECRET_KEY") or secrets.token_hex(32)

    DATA_DIR = Path(os.environ.get("REVUE_AD_DATA_DIR", BASE_DIR / "instance"))
    CAMPAGNES_DIR = DATA_DIR / "campagnes"
    JOURNAL = DATA_DIR / "journal.log"
    MAPPING_COLONNES = Path(os.environ.get("REVUE_AD_MAPPING", BASE_DIR / "config" / "mapping_colonnes.json"))
    REGLES = Path(os.environ.get("REVUE_AD_REGLES", BASE_DIR / "config" / "regles.json"))

    MAX_CONTENT_LENGTH = int(os.environ.get("REVUE_AD_MAX_MO", "25")) * 1024 * 1024
    MAX_FICHIERS = int(os.environ.get("REVUE_AD_MAX_FICHIERS", "20"))
    EXTENSIONS_AUTORISEES = {".csv", ".xlsx"}
    LIGNES_APERCU = 12

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    # À passer à True si l'outil est servi en HTTPS sur un serveur interne
    SESSION_COOKIE_SECURE = os.environ.get("REVUE_AD_HTTPS", "0") == "1"
