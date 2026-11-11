"""Règles paramétrables : nomenclature des identifiants et interprétation du statut.

La classe Nomenclature lit ses préfixes dans config/regles.json : si la DOQSI
change de convention, on modifie le fichier, pas le code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ingestion.lecteur import normaliser

SEPARATEURS_IDENTIFIANT = re.compile(r"[_\-.\s]")


@dataclass(frozen=True)
class Categorie:
    cle: str
    libelle: str
    libelle_pluriel: str
    prefixe: str | None
    temporaire: bool


@dataclass(frozen=True)
class ResultatNomenclature:
    categorie: str          # clé de la catégorie retenue (ou probable si non conforme)
    conforme: bool
    motif: str = ""         # explication lisible quand l'identifiant n'est pas conforme


class Nomenclature:
    """Classe un identifiant selon les préfixes configurés.

    - « stagiaire_Ngono »   -> stagiaire, conforme
    - « STAGIAIRE_Ngono »   -> stagiaire, conforme (l'AD ignore la casse)
    - « stagiaire_ »        -> stagiaire, non conforme (nom absent)
    - « stagiare_Ngono »    -> stagiaire probable, non conforme (préfixe mal orthographié)
    - « stagiaire-Ngono »   -> stagiaire probable, non conforme (mauvais séparateur)
    - « ngono »             -> catégorie par défaut (employé), conforme
    """

    def __init__(self, categories: list[Categorie], defaut: str, seuil_similarite: float = 0.75):
        if defaut not in {c.cle for c in categories}:
            raise ValueError(f"Catégorie par défaut inconnue : {defaut}")
        self.categories = {c.cle: c for c in categories}
        self.defaut = defaut
        self.seuil = seuil_similarite
        # Préfixes du plus long au plus court, pour qu'un préfixe ne masque pas un autre
        self._prefixees = sorted((c for c in categories if c.prefixe), key=lambda c: -len(c.prefixe))

    def classer(self, identifiant: str) -> ResultatNomenclature:
        ident = (identifiant or "").strip()
        bas = ident.lower()

        for cat in self._prefixees:
            prefixe = cat.prefixe.lower()
            if bas.startswith(prefixe):
                reste = ident[len(prefixe):]
                if not reste.strip(" _-."):
                    return ResultatNomenclature(cat.cle, False, f"Préfixe « {cat.prefixe} » sans nom après.")
                if " " in reste:
                    return ResultatNomenclature(cat.cle, False, "L'identifiant contient un espace.")
                return ResultatNomenclature(cat.cle, True)

        # Pas de préfixe exact : l'identifiant ressemble-t-il à un préfixe mal écrit ?
        tete = SEPARATEURS_IDENTIFIANT.split(bas, maxsplit=1)[0]
        a_un_separateur = bool(SEPARATEURS_IDENTIFIANT.search(bas))
        for cat in self._prefixees:
            racine = cat.prefixe.lower().rstrip("_-. ")
            if not tete:
                break
            if tete == racine and a_un_separateur:
                return ResultatNomenclature(cat.cle, False,
                                            f"Séparateur incorrect : la convention est « {cat.prefixe}nom ».")
            # On ne compare que si l'identifiant a un séparateur ou commence comme la racine,
            # pour ne pas confondre un nom d'employé avec un préfixe.
            if a_un_separateur or bas.startswith(racine[:4]):
                ressemblance = SequenceMatcher(None, tete, racine).ratio()
                if ressemblance >= self.seuil or (len(tete) >= 4 and racine.startswith(tete) and a_un_separateur):
                    return ResultatNomenclature(cat.cle, False,
                                                f"Préfixe « {tete} » proche de « {cat.prefixe} » : "
                                                f"probablement un compte {cat.libelle.lower()} mal nommé.")
        return ResultatNomenclature(self.defaut, True)

    def libelle(self, cle: str, pluriel: bool = False) -> str:
        c = self.categories.get(cle)
        if not c:
            return cle
        return c.libelle_pluriel if pluriel else c.libelle

    def est_temporaire(self, cle: str) -> bool:
        c = self.categories.get(cle)
        return bool(c and c.temporaire)


class Regles:
    """Ensemble des paramètres de la revue, chargés depuis config/regles.json."""

    def __init__(self, donnees: dict):
        self.brut = donnees
        cats = [Categorie(cle, d["libelle"], d.get("libelle_pluriel", d["libelle"] + "s"),
                          d.get("prefixe"), bool(d.get("temporaire")))
                for cle, d in donnees["categories"].items()]
        self.nomenclature = Nomenclature(cats, donnees["categorie_par_defaut"],
                                         float(donnees.get("seuil_similarite_prefixe", 0.75)))
        dorm = donnees.get("dormance", {})
        self.seuil_dormance = int(dorm.get("seuil_jours", 90))
        self.inclure_jamais_connectes = bool(dorm.get("inclure_jamais_connectes", True))
        self.dormance_exclut_desactives = bool(dorm.get("exclure_desactives", True))
        self.dormance_exclut_expires = bool(dorm.get("exclure_expires", True))
        self.signaler_expires_non_desactives = bool(donnees.get("signaler_expires_non_desactives", True))
        statuts = donnees.get("valeurs_statut", {})
        self._actif = {normaliser(v) for v in statuts.get("actif", [])}
        self._desactive = {normaliser(v) for v in statuts.get("desactive", [])}

    @classmethod
    def depuis_fichier(cls, chemin: Path) -> "Regles":
        with open(chemin, encoding="utf-8") as f:
            return cls(json.load(f))

    def statut(self, valeur: str) -> str:
        """'actif', 'desactive' ou 'inconnu'."""
        v = normaliser(valeur or "")
        if not v:
            return "inconnu"
        if v in self._desactive:
            return "desactive"
        if v in self._actif:
            return "actif"
        # Valeurs composées du type « Disabled, Locked » ou « Enabled - Expired »
        if any(d and d in v for d in self._desactive if len(d) > 2):
            return "desactive"
        if any(a and a in v for a in self._actif if len(a) > 2):
            return "actif"
        return "inconnu"
