"""Contrôle qualité des exports, avant toute catégorisation ou détection d'anomalie.

Le but n'est pas encore de juger les comptes, mais de savoir si les données
sont exploitables : identifiants manquants, doublons, dates illisibles, etc.
Chaque constat garde les numéros de ligne du fichier d'origine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .lecteur import COLONNE_LIGNE, CorrespondanceColonnes, ResultatLecture

# Valeurs qui signifient « pas de date » de façon voulue (ex. compte sans expiration)
VALEURS_JAMAIS = {"never", "jamais", "aucune", "aucun", "notset", "nonset", "nondefinie", "noexpiry"}
# Valeurs qui signifient simplement « non renseigné »
VALEURS_VIDES = {"", "-", "--", "na", "n/a", "null", "none", "nan", "nat"}

# Formats rencontrés dans les exports Windows / ManageEngine.
# HYPOTHESE : les dates à barres obliques sont lues jour/mois/année (usage camerounais).
FORMATS_DATE = (
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S",
    "%b %d, %Y %I:%M:%S %p", "%b %d, %Y %I:%M %p", "%b %d, %Y",
    "%d %b %Y %H:%M:%S", "%d %b %Y",
)

MAX_EXEMPLES = 5


def analyser_date(valeur: str) -> tuple[str, datetime | None]:
    """Classe une valeur : 'vide', 'jamais', 'valide' ou 'invalide'."""
    brut = (valeur or "").strip()
    cle = brut.lower().replace(" ", "")
    if cle in VALEURS_VIDES:
        return "vide", None
    if cle.replace("'", "") in VALEURS_JAMAIS:
        return "jamais", None
    for fmt in FORMATS_DATE:
        try:
            return "valide", datetime.strptime(brut, fmt)
        except ValueError:
            continue
    try:
        return "valide", datetime.fromisoformat(brut)
    except ValueError:
        return "invalide", None


@dataclass
class Constat:
    niveau: str            # 'erreur' (bloquant), 'alerte' (à corriger), 'info'
    titre: str
    detail: str = ""
    nombre: int = 0
    exemples: list[str] = field(default_factory=list)

    def en_dict(self) -> dict:
        return self.__dict__.copy()


ORDRE_NIVEAUX = {"erreur": 0, "alerte": 1, "info": 2}


def _lignes(df: pd.DataFrame, masque) -> list[str]:
    return [str(n) for n in df.loc[masque, COLONNE_LIGNE].head(MAX_EXEMPLES)]


def _texte_lignes(lignes: list[str], total: int) -> str:
    if not lignes:
        return ""
    suite = f" et {total - len(lignes)} autre(s)" if total > len(lignes) else ""
    return f"Ligne(s) {', '.join(lignes)}{suite}."


def controler(resultat: ResultatLecture, correspondance: CorrespondanceColonnes,
              maintenant: datetime | None = None) -> list[Constat]:
    maintenant = maintenant or datetime.now()
    df = resultat.donnees
    constats: list[Constat] = []

    if df.empty:
        constats.append(Constat("erreur", "Aucune ligne de données",
                                "Le fichier ne contient qu'un en-tête ou des lignes vides."))
        return constats

    for champ in resultat.champs_obligatoires_absents:
        constats.append(Constat(
            "erreur", f"Colonne « {correspondance.libelle(champ)} » introuvable",
            "Sans elle, les comptes ne peuvent pas être identifiés. Ajoutez l'intitulé exact "
            "de la colonne dans config/mapping_colonnes.json."))

    if resultat.colonnes_non_reconnues:
        noms = resultat.colonnes_non_reconnues
        constats.append(Constat(
            "info", f"{len(noms)} colonne(s) non rattachée(s) à un champ connu",
            "Elles sont conservées mais pas encore exploitées : " + ", ".join(f"« {n} »" for n in noms[:8])
            + (" …" if len(noms) > 8 else "") + ".", len(noms)))

    col_id = resultat.colonne_du_champ("identifiant")
    if col_id:
        ids = df[col_id]
        vides = ids == ""
        if vides.any():
            n = int(vides.sum())
            constats.append(Constat("alerte", f"{n} ligne(s) sans identifiant",
                                    _texte_lignes(_lignes(df, vides), n), n))
        cle = ids.str.lower()
        doublons = (cle != "") & cle.duplicated(keep=False)
        if doublons.any():
            # Un seul représentant par identifiant : « Jdupont » et « jdupont » sont le même compte AD
            par_cle = {}
            for v in ids[doublons]:
                par_cle.setdefault(v.lower(), v)
            noms = sorted(par_cle.values(), key=str.lower)
            constats.append(Constat(
                "alerte", f"{len(noms)} identifiant(s) présent(s) plusieurs fois",
                f"{int(doublons.sum())} lignes concernées. Un compte en double fausserait les totaux du rapport.",
                len(noms), noms[:MAX_EXEMPLES]))

    sans_ligne = df.drop(columns=[COLONNE_LIGNE])
    copies = sans_ligne.duplicated(keep="first")
    if copies.any():
        n = int(copies.sum())
        constats.append(Constat("info", f"{n} ligne(s) strictement identique(s) à une autre",
                                _texte_lignes(_lignes(df, copies), n), n))

    dates_par_champ: dict[str, pd.Series] = {}
    for col, champ in resultat.correspondance.items():
        if correspondance.type_de(champ) != "date":
            continue
        analyses = df[col].map(analyser_date)
        statuts = analyses.map(lambda a: a[0])
        dates_par_champ[champ] = analyses.map(lambda a: a[1])
        libelle = correspondance.libelle(champ)

        invalides = statuts == "invalide"
        if invalides.any():
            n = int(invalides.sum())
            valeurs = sorted(set(df.loc[invalides, col]))[:MAX_EXEMPLES]
            constats.append(Constat("alerte", f"{n} date(s) illisible(s) dans « {libelle} »",
                                    _texte_lignes(_lignes(df, invalides), n), n, valeurs))
        vides_d = statuts == "vide"
        if vides_d.any():
            n = int(vides_d.sum())
            constats.append(Constat("alerte" if champ == "date_creation" else "info",
                                    f"{n} valeur(s) vide(s) dans « {libelle} »",
                                    _texte_lignes(_lignes(df, vides_d), n), n))
        jamais = statuts == "jamais"
        if jamais.any():
            n = int(jamais.sum())
            sens = "aucune expiration" if champ == "date_expiration" else "aucune valeur"
            constats.append(Constat("info", f"{n} compte(s) marqué(s) « jamais » dans « {libelle} »",
                                    f"Interprété comme {sens}.", n))
        if champ in ("date_creation", "derniere_connexion"):
            futures = dates_par_champ[champ].map(lambda d: d is not None and d > maintenant)
            if futures.any():
                n = int(futures.sum())
                constats.append(Constat("alerte", f"{n} date(s) dans le futur dans « {libelle} »",
                                        "Vérifiez le format jour/mois : une inversion produit ce symptôme. "
                                        + _texte_lignes(_lignes(df, futures), n), n))

    if "date_creation" in dates_par_champ and "date_expiration" in dates_par_champ:
        creation, expiration = dates_par_champ["date_creation"], dates_par_champ["date_expiration"]
        incoherent = pd.Series([c is not None and e is not None and e < c
                                for c, e in zip(creation, expiration)], index=df.index)
        if incoherent.any():
            n = int(incoherent.sum())
            constats.append(Constat("alerte", f"{n} compte(s) expiré(s) avant leur création",
                                    _texte_lignes(_lignes(df, incoherent), n), n))

    constats.sort(key=lambda c: ORDRE_NIVEAUX[c.niveau])
    return constats


def identifiants(resultat: ResultatLecture) -> set[str]:
    col = resultat.colonne_du_champ("identifiant")
    if not col:
        return set()
    return {v.lower() for v in resultat.donnees[col] if v}
