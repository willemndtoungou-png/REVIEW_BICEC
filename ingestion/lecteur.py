"""Lecture des exports CSV et XLSX produits par ADAudit Plus.

Le module ne fait aucune hypothèse rigide sur le format exact des exports :
- encodage détecté parmi UTF-8, Windows-1252 et Latin-1 ;
- séparateur CSV détecté parmi ; , tabulation et | ;
- ligne d'en-tête recherchée dans les premières lignes (certains exports
  commencent par un titre de rapport ou une date de génération) ;
- colonnes rattachées aux champs internes grâce à config/mapping_colonnes.json.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ENCODAGES = ("utf-8-sig", "cp1252", "latin-1")
SEPARATEURS = (";", ",", "\t", "|")
LIGNES_SONDEES = 25          # nombre de lignes inspectées pour trouver l'en-tête
SIGNATURE_XLSX = b"PK\x03\x04"  # un .xlsx est une archive ZIP
COLONNE_LIGNE = "__ligne"      # numéro de ligne dans le fichier d'origine


class ErreurLecture(Exception):
    """Fichier illisible ou non conforme à ce que l'outil sait traiter."""


def normaliser(texte: str) -> str:
    """Met un intitulé sous une forme comparable : sans accents, minuscules, sans ponctuation."""
    texte = unicodedata.normalize("NFKD", str(texte))
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", texte.lower())


class CorrespondanceColonnes:
    """Rattache les colonnes d'un export aux champs internes de l'outil.

    La définition vient d'un fichier JSON modifiable sans toucher au code,
    pour suivre l'évolution des exports ADAudit Plus.
    """

    def __init__(self, champs: dict[str, dict]):
        self.champs = champs
        self._index: dict[str, str] = {}
        for nom, definition in champs.items():
            for alias in [nom, *definition.get("alias", [])]:
                self._index.setdefault(normaliser(alias), nom)

    @classmethod
    def depuis_fichier(cls, chemin: Path) -> "CorrespondanceColonnes":
        with open(chemin, encoding="utf-8") as f:
            return cls(json.load(f)["champs"])

    def champ_pour(self, colonne: str) -> str | None:
        return self._index.get(normaliser(colonne))

    def score(self, cellules: list[str]) -> int:
        """Nombre de cellules d'une ligne qui ressemblent à un intitulé connu."""
        return sum(1 for c in cellules if c and self.champ_pour(c))

    def associer(self, colonnes: list[str]) -> tuple[dict[str, str], list[str], list[str]]:
        """Retourne (colonne -> champ, colonnes non reconnues, champs obligatoires absents)."""
        correspondance: dict[str, str] = {}
        non_reconnues: list[str] = []
        deja_pris: set[str] = set()
        for col in colonnes:
            champ = self.champ_pour(col)
            if champ and champ not in deja_pris:
                correspondance[col] = champ
                deja_pris.add(champ)
            else:
                non_reconnues.append(col)
        absents = [n for n, d in self.champs.items() if d.get("obligatoire") and n not in deja_pris]
        return correspondance, non_reconnues, absents

    def type_de(self, champ: str) -> str:
        return self.champs.get(champ, {}).get("type", "texte")

    def libelle(self, champ: str) -> str:
        return self.champs.get(champ, {}).get("libelle", champ)


@dataclass
class ResultatLecture:
    nom_fichier: str
    format: str
    donnees: pd.DataFrame
    ligne_entete: int                      # numéro (1 = première ligne) de la ligne d'en-tête
    correspondance: dict[str, str]
    colonnes_non_reconnues: list[str]
    champs_obligatoires_absents: list[str]
    encodage: str | None = None
    separateur: str | None = None
    feuille: str | None = None
    avertissements: list[str] = field(default_factory=list)

    @property
    def nb_lignes(self) -> int:
        return len(self.donnees)

    def colonne_du_champ(self, champ: str) -> str | None:
        for col, c in self.correspondance.items():
            if c == champ:
                return col
        return None


# --------------------------------------------------------------------------- CSV

def _decoder(contenu: bytes) -> tuple[str, str]:
    for enc in ENCODAGES:
        try:
            return contenu.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ErreurLecture("Encodage du fichier non reconnu.")  # latin-1 ne peut pas échouer, sécurité


def _detecter_separateur(texte: str) -> str:
    echantillon = "\n".join(texte.splitlines()[:LIGNES_SONDEES])
    try:
        return csv.Sniffer().sniff(echantillon, delimiters="".join(SEPARATEURS)).delimiter
    except csv.Error:
        # Repli : le séparateur le plus fréquent et le plus régulier d'une ligne à l'autre
        lignes = [l for l in echantillon.splitlines() if l.strip()]
        meilleur, meilleur_score = ",", -1
        for sep in SEPARATEURS:
            comptes = [l.count(sep) for l in lignes]
            if not comptes or max(comptes) == 0:
                continue
            score = sum(1 for c in comptes if c == max(comptes))
            if score > meilleur_score:
                meilleur, meilleur_score = sep, score
        return meilleur


def _trouver_entete(lignes: list[list[str]], correspondance: CorrespondanceColonnes) -> int:
    """Index (0 = première ligne) de la ligne qui ressemble le plus à un en-tête."""
    lignes = lignes[:LIGNES_SONDEES]
    if not lignes:
        return 0
    scores = [correspondance.score([str(c).strip() for c in l]) for l in lignes]
    if max(scores) > 0:
        return scores.index(max(scores))
    # Aucun intitulé connu : première ligne suffisamment remplie
    largeur = max(sum(1 for c in l if str(c).strip()) for l in lignes)
    for i, l in enumerate(lignes):
        if sum(1 for c in l if str(c).strip()) >= max(2, int(largeur * 0.6)):
            return i
    return 0


def _nettoyer(df: pd.DataFrame, ligne_entete: int) -> tuple[pd.DataFrame, list[str]]:
    avertissements = []
    df = df.copy()
    df.columns = [str(c).strip() if str(c).strip() and not str(c).startswith("Unnamed") else f"Colonne {i + 1}"
                  for i, c in enumerate(df.columns)]
    # Intitulés en double : on les rend uniques pour ne rien écraser
    vus: dict[str, int] = {}
    colonnes = []
    for c in df.columns:
        if c in vus:
            vus[c] += 1
            colonnes.append(f"{c} ({vus[c]})")
            avertissements.append(f"La colonne « {c} » apparaît plusieurs fois.")
        else:
            vus[c] = 1
            colonnes.append(c)
    df.columns = colonnes
    df = df.fillna("").astype(str).apply(lambda s: s.str.strip())
    # Numéro de ligne réel dans le fichier, pour que l'utilisateur retrouve la ligne
    df.insert(0, COLONNE_LIGNE, range(ligne_entete + 2, ligne_entete + 2 + len(df)))
    vides = (df.drop(columns=[COLONNE_LIGNE]) == "").all(axis=1)
    if vides.any():
        df = df[~vides]
    colonnes_vides = [c for c in df.columns if c != COLONNE_LIGNE and (df[c] == "").all()]
    if colonnes_vides and len(df):
        df = df.drop(columns=colonnes_vides)
        avertissements.append(f"{len(colonnes_vides)} colonne(s) entièrement vide(s) ignorée(s).")
    return df.reset_index(drop=True), avertissements


def lire_csv(contenu: bytes, nom: str, correspondance: CorrespondanceColonnes) -> ResultatLecture:
    texte, encodage = _decoder(contenu)
    if "\x00" in texte:
        raise ErreurLecture("Le fichier contient des octets binaires : ce n'est pas un CSV texte.")
    sep = _detecter_separateur(texte)
    lignes = list(csv.reader(io.StringIO("\n".join(texte.splitlines()[:LIGNES_SONDEES])), delimiter=sep))
    idx = _trouver_entete(lignes, correspondance)
    try:
        df = pd.read_csv(io.StringIO(texte), sep=sep, skiprows=idx, dtype=str,
                         keep_default_na=False, skip_blank_lines=False, on_bad_lines="warn",
                         engine="python")
    except (pd.errors.ParserError, ValueError) as e:
        raise ErreurLecture(f"Structure CSV illisible : {e}") from e
    df, avert = _nettoyer(df, idx)
    corr, non_rec, absents = correspondance.associer([c for c in df.columns if c != COLONNE_LIGNE])
    if idx > 0:
        avert.insert(0, f"{idx} ligne(s) de titre ignorée(s) avant l'en-tête.")
    return ResultatLecture(nom, "CSV", df, idx + 1, corr, non_rec, absents,
                           encodage=encodage, separateur=sep, avertissements=avert)


# --------------------------------------------------------------------------- XLSX

def lire_xlsx(contenu: bytes, nom: str, correspondance: CorrespondanceColonnes) -> ResultatLecture:
    if not contenu.startswith(SIGNATURE_XLSX):
        raise ErreurLecture("Le fichier porte l'extension .xlsx mais n'est pas un classeur Excel valide.")
    try:
        feuilles = pd.read_excel(io.BytesIO(contenu), sheet_name=None, header=None,
                                 dtype=str, engine="openpyxl")
    except Exception as e:  # openpyxl lève des exceptions variées sur un fichier corrompu
        raise ErreurLecture(f"Classeur Excel illisible : {type(e).__name__}") from e
    if not feuilles:
        raise ErreurLecture("Le classeur ne contient aucune feuille.")

    # On retient la feuille qui ressemble le plus à un export de comptes
    meilleure, meilleur_score, meilleur_idx = None, -1, 0
    for nom_feuille, brut in feuilles.items():
        lignes = brut.head(LIGNES_SONDEES).fillna("").astype(str).values.tolist()
        idx = _trouver_entete(lignes, correspondance)
        score = correspondance.score(lignes[idx]) if lignes else -1
        if score > meilleur_score:
            meilleure, meilleur_score, meilleur_idx = nom_feuille, score, idx

    brut = feuilles[meilleure]
    entete = brut.iloc[meilleur_idx].fillna("").astype(str).tolist()
    df = brut.iloc[meilleur_idx + 1:].copy()
    df.columns = entete
    df, avert = _nettoyer(df, meilleur_idx)
    # Excel renvoie les dates sous la forme « 2026-01-05 00:00:00 » : on retire l'heure nulle
    for c in df.columns:
        if c != COLONNE_LIGNE:
            df[c] = df[c].str.replace(r"^(\d{4}-\d{2}-\d{2}) 00:00:00$", r"\1", regex=True)
    corr, non_rec, absents = correspondance.associer([c for c in df.columns if c != COLONNE_LIGNE])
    if len(feuilles) > 1:
        avert.insert(0, f"Classeur de {len(feuilles)} feuilles : la feuille « {meilleure} » a été retenue.")
    if meilleur_idx > 0:
        avert.insert(0, f"{meilleur_idx} ligne(s) de titre ignorée(s) avant l'en-tête.")
    return ResultatLecture(nom, "XLSX", df, meilleur_idx + 1, corr, non_rec, absents,
                           feuille=str(meilleure), avertissements=avert)


def lire_fichier(chemin: Path, nom_affiche: str, correspondance: CorrespondanceColonnes) -> ResultatLecture:
    contenu = Path(chemin).read_bytes()
    if not contenu.strip():
        raise ErreurLecture("Le fichier est vide.")
    ext = Path(nom_affiche).suffix.lower()
    if ext == ".csv":
        return lire_csv(contenu, nom_affiche, correspondance)
    if ext == ".xlsx":
        return lire_xlsx(contenu, nom_affiche, correspondance)
    raise ErreurLecture(f"Format « {ext} » non pris en charge (attendu : .csv ou .xlsx).")
