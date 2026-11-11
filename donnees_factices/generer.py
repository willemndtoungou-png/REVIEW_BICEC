"""Génère des exports CSV FACTICES qui imitent ceux d'ADAudit Plus, pour tester l'outil
sans jamais manipuler de vraies données de la banque.

    python donnees_factices/generer.py

Nomenclatures (confirmées par l'encadreur) : stagiaire_<nom>, prestataire_<nom>,
employé = nom sans préfixe.

HYPOTHESES (à remplacer dès qu'un export réel anonymisé est disponible) :
intitulés de colonnes, format des dates, lignes de titre avant l'en-tête.
Tous les noms sont inventés.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

ICI = Path(__file__).resolve().parent
REFERENCE = datetime(2026, 11, 10, 9, 0)
alea = random.Random(2026)

NOMS = ["Ngono", "Mbarga", "Ekotto", "Fotso", "Tchouaga", "Abena", "Essomba", "Nkoulou", "Mvondo",
        "Djomo", "Kamga", "Bella", "Owona", "Ndzana", "Etoundi", "Manga", "Kouam", "Ateba", "Onana", "Biyong",
        "Tsala", "Eyenga", "Nguema", "Mballa", "Zambo", "Ondoa", "Mekongo", "Nana", "Simo", "Tagne"]
PRENOMS = ["Alice", "Brice", "Carine", "Didier", "Estelle", "Franck", "Grace", "Hervé", "Inès", "Joël",
           "Karl", "Laure", "Marc", "Nadia", "Olivier", "Pauline", "Rodrigue", "Sandrine", "Thierry", "Yvette"]
OPERATEURS = ["adm_factice1", "adm_factice2", "adm_factice3"]


def d(dt: datetime | str | None) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def jours(n: int) -> datetime:
    return REFERENCE - timedelta(days=n)


def compte(ident, nom, cree, expire, connexion, statut="Enabled", groupes="Utilisateurs du domaine"):
    return {"id": ident, "nom": nom, "cree": cree, "expire": expire, "connexion": connexion,
            "statut": statut, "groupes": groupes}


def comptes() -> list[dict]:
    lignes = []
    noms = NOMS[:]
    alea.shuffle(noms)

    # ---- Employés : nom sans préfixe, pas d'expiration
    vus = set()
    while len(lignes) < 40:
        p, n = alea.choice(PRENOMS), alea.choice(NOMS)
        ident = n.lower() if n.lower() not in vus else (p[0] + n).lower()
        if ident in vus:
            continue
        vus.add(ident)
        dormant = alea.random() < 0.12
        lignes.append(compte(ident, f"{p} {n}", jours(alea.randint(200, 3000)), "Never",
                             jours(alea.randint(95, 400) if dormant else alea.randint(0, 20))))
    # quelques employés partis, déjà désactivés (pas dormants au sens de la règle)
    for l in lignes[:3]:
        l["statut"], l["connexion"] = "Disabled", jours(alea.randint(120, 300))

    # ---- Stagiaires : stagiaire_<Nom>, expiration à la fin du stage
    for n in noms[:14]:
        cree = jours(alea.randint(10, 200))
        expire = cree + timedelta(days=140)
        expire_passe = expire < REFERENCE
        lignes.append(compte(
            f"stagiaire_{n}", f"{alea.choice(PRENOMS)} {n}", cree, expire,
            (expire - timedelta(days=alea.randint(1, 6))) if expire_passe else jours(alea.randint(0, 5)),
            # Dans l'AD, un compte expiré reste « Enabled » tant qu'on ne le désactive pas
            statut="Disabled" if expire_passe and alea.random() < 0.6 else "Enabled",
            groupes="Utilisateurs du domaine;GG_Stagiaires"))

    # ---- Prestataires : prestataire_<Nom>
    for n in noms[14:22]:
        cree = jours(alea.randint(30, 300))
        expire = cree + timedelta(days=alea.choice([90, 180, 365]))
        expire_passe = expire < REFERENCE
        lignes.append(compte(
            f"prestataire_{n}", f"{alea.choice(PRENOMS)} {n}", cree, expire,
            (expire - timedelta(days=alea.randint(1, 20))) if expire_passe else jours(alea.randint(0, 30)),
            statut="Disabled" if expire_passe and alea.random() < 0.5 else "Enabled",
            groupes="Utilisateurs du domaine;GG_Prestataires"))

    # ---- Cas volontaires pour les règles de la revue
    lignes += [
        compte("stagiaire_SansFin", "Stagiaire sans expiration", jours(20), "Never", jours(1),
               groupes="GG_Stagiaires"),                                        # notification
        compte("prestataire_Permanent", "Prestataire sans expiration", jours(400), "", jours(3),
               groupes="GG_Prestataires"),                                      # notification
        compte("tchoumi", "Employé avec date d'expiration", jours(60), REFERENCE + timedelta(days=50),
               jours(2)),                                                       # notification
        compte("stagiare_Mbappe", "Préfixe mal orthographié", jours(30), REFERENCE + timedelta(days=90),
               jours(1), groupes="GG_Stagiaires"),                              # nomenclature
        compte("prestataire-Nkeng", "Mauvais séparateur", jours(45), REFERENCE + timedelta(days=120),
               jours(4), groupes="GG_Prestataires"),                            # nomenclature
        compte("presta_Ewane", "Préfixe abrégé", jours(80), REFERENCE + timedelta(days=40), jours(6)),
        compte("stagiaire_", "Préfixe sans nom", jours(15), REFERENCE + timedelta(days=100), jours(2)),
        compte("ebogo", "Jamais connecté", jours(240), "Never", None),          # jamais utilisé
        compte("prestataire_Retour", "Connexion après expiration", jours(200), jours(30), jours(5),
               groupes="GG_Prestataires"),                                      # connexion après expiration
    ]

    # ---- Défauts de qualité (contrôle qualité)
    lignes.append(dict(lignes[5]))                                              # ligne strictement identique
    lignes.append({**lignes[45], "nom": "Doublon d'identifiant"})               # même id, autre ligne
    lignes.append(compte("", "Compte sans identifiant", jours(50), "Never", REFERENCE))
    lignes.append(compte("stagiaire_DateFausse", "Date impossible", "31/02/2026 10:00:00",
                         REFERENCE + timedelta(days=30), REFERENCE))
    return lignes


def ecrire_utilisateurs(lignes: list[dict], chemin: Path) -> None:
    """Rapport « tous les utilisateurs » : Windows-1252, point-virgule, titre avant l'en-tête."""
    with open(chemin, "w", encoding="cp1252", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Rapport : Tous les utilisateurs (DONNÉES FACTICES)"])
        w.writerow([f"Généré le {d(REFERENCE)}"])
        w.writerow([])
        w.writerow(["SAM Account Name", "Display Name", "Account Status", "Created Time", "Account Expires",
                    "Last Logon Time", "Member Of", "Password Never Expires"])
        for l in lignes:
            w.writerow([l["id"], l["nom"], l["statut"], d(l["cree"]), d(l["expire"]), d(l["connexion"]),
                        l["groupes"], "False"])


def ecrire_creations(lignes: list[dict], chemin: Path) -> None:
    """Rapport « comptes créés » : qui a créé quel compte (champ Opérateur)."""
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=",")
        w.writerow(["Comptes utilisateurs créés (DONNÉES FACTICES)"])
        w.writerow([])
        w.writerow(["Created User", "Caller User Name", "Time", "Domain Controller"])
        for l in lignes:
            if not l["id"] or isinstance(l["cree"], str):
                continue
            w.writerow([l["id"], alea.choice(OPERATEURS), d(l["cree"]), "DC-FACTICE-01"])


def ecrire_erreurs(dossier: Path) -> None:
    dossier.mkdir(exist_ok=True)
    (dossier / "colonnes_inconnues.csv").write_text("Col A,Col B\n1,2\n3,4\n", encoding="utf-8")
    (dossier / "binaire.csv").write_bytes(b"\x00\x01\x02 pas un CSV")


if __name__ == "__main__":
    for ancien in ("export_comptes_crees_factice.xlsx",):
        (ICI / ancien).unlink(missing_ok=True)
    (ICI / "cas_d_erreur" / "faux_classeur.xlsx").unlink(missing_ok=True)
    lignes = comptes()
    ecrire_utilisateurs(lignes, ICI / "export_utilisateurs_factice.csv")
    ecrire_creations(lignes, ICI / "export_comptes_crees_factice.csv")
    ecrire_erreurs(ICI / "cas_d_erreur")
    print(f"{len(lignes)} lignes factices écrites dans {ICI}")
