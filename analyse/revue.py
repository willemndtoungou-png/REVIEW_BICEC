"""Revue des comptes : fusion des exports, catégorisation, anomalies et notifications.

Entrée  : les fichiers lus par ingestion.lecteur (un export = un ResultatLecture).
Sortie  : un objet Revue sérialisable en JSON, utilisé par l'interface et le rapport PDF.

Vocabulaire :
- une ANOMALIE est un écart qui présente un risque ou demande une action (compte dormant…) ;
- une NOTIFICATION signale une contradiction entre les indices (le nom dit « stagiaire »
  mais le compte n'a pas de date d'expiration) : à vérifier, sans conclure à un risque.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time

from ingestion.lecteur import ResultatLecture
from ingestion.qualite import analyser_date

from .regles import Regles

# Champs repris des exports dans la fiche consolidée d'un compte
CHAMPS_COMPTE = ("nom_affiche", "upn", "statut", "date_creation", "date_expiration",
                 "derniere_connexion", "operateur", "groupes", "mot_de_passe_jamais_expire")
CHAMPS_DATE = ("date_creation", "date_expiration", "derniere_connexion")

# Définition des types de constats : code -> (famille, gravité, titre, explication)
TYPES = {
    "dormant": ("anomalie", "moyenne", "Comptes actifs dormants",
                "Comptes actifs sans connexion depuis plus de {seuil} jours. Un compte inutilisé mais "
                "ouvert peut être utilisé à l'insu de son titulaire."),
    "jamais_connecte": ("anomalie", "moyenne", "Comptes actifs jamais utilisés",
                        "Comptes actifs créés depuis plus de {seuil} jours et sans aucune connexion enregistrée."),
    "connexion_apres_expiration": ("anomalie", "elevee", "Connexions après la date d'expiration",
                                   "Dernière connexion postérieure à la date d'expiration du compte. "
                                   "Soit la date a été modifiée après coup, soit l'export est incohérent : "
                                   "à vérifier en priorité."),
    "nomenclature": ("anomalie", "moyenne", "Identifiants non conformes à la nomenclature",
                     "Identifiants qui ne respectent pas la convention de nommage de la DOQSI. "
                     "La catégorie indiquée est la catégorie probable."),
    "expire_non_desactive": ("anomalie", "faible", "Comptes expirés non désactivés",
                             "Comptes dont la date d'expiration est passée mais qui restent à l'état « actif ». "
                             "L'AD refuse déjà la connexion : il s'agit de nettoyage (désactivation ou suppression)."),
    "temporaire_sans_expiration": ("notification", "", "Comptes temporaires sans date d'expiration",
                                   "L'identifiant désigne un stagiaire ou un prestataire, mais aucune date "
                                   "d'expiration n'est renseignée (vide ou « Never »)."),
    "employe_avec_expiration": ("notification", "", "Comptes sans préfixe avec une date d'expiration",
                                "L'identifiant ne porte pas de préfixe (employé), mais le compte a une date "
                                "d'expiration, comme un compte temporaire. Possible stagiaire ou prestataire mal nommé."),
}
ORDRE_GRAVITE = {"elevee": 0, "moyenne": 1, "faible": 2, "": 3}
LIBELLE_GRAVITE = {"elevee": "Élevée", "moyenne": "Moyenne", "faible": "Faible", "": "À vérifier"}
LIBELLE_STATUT = {"actif": "Actif", "desactive": "Désactivé", "inconnu": "Non renseigné"}


@dataclass
class Compte:
    identifiant: str
    categorie: str
    conforme: bool
    motif_nomenclature: str = ""
    nom_affiche: str = ""
    upn: str = ""
    statut_brut: str = ""
    statut: str = "inconnu"
    date_creation: str | None = None         # ISO 8601
    date_expiration: str | None = None
    sans_expiration: bool = False            # « Never », vide : pas de date d'expiration
    derniere_connexion: str | None = None
    operateur: str = ""
    groupes: str = ""
    mot_de_passe_jamais_expire: str = ""
    sources: list[str] = field(default_factory=list)
    # Champs présents dans au moins un export contenant ce compte. Un compte vu seulement
    # dans le rapport des créations n'a pas d'information de connexion : on ne doit pas
    # en conclure qu'il ne s'est jamais connecté.
    champs_connus: list[str] = field(default_factory=list)

    def date(self, champ: str) -> datetime | None:
        v = getattr(self, champ)
        return datetime.fromisoformat(v) if v else None

    def expire_le(self, reference: datetime) -> bool:
        d = self.date("date_expiration")
        return d is not None and d < reference

    def etat(self, reference: datetime) -> str:
        """État au sens du rapport DOQSI : Ouvert ou Fermé."""
        if self.statut == "desactive" or self.expire_le(reference):
            return "Fermé"
        if self.statut == "actif":
            return "Ouvert"
        return "Non déterminé"


@dataclass
class Constat:
    code: str
    identifiant: str
    detail: str

    @property
    def famille(self) -> str:
        return TYPES[self.code][0]

    @property
    def gravite(self) -> str:
        return TYPES[self.code][1]


@dataclass
class Revue:
    date_reference: str
    comptes: list[Compte]
    constats: list[Constat]
    champs_disponibles: list[str]
    regles_non_evaluees: list[str]
    conflits: list[str]
    parametres: dict

    # --------------------------------------------------------------- lecture

    def reference(self) -> datetime:
        return datetime.fromisoformat(self.date_reference)

    def compte(self, identifiant: str) -> Compte | None:
        return self._index().get(identifiant.lower())

    def _index(self) -> dict[str, Compte]:
        if not hasattr(self, "_idx"):
            self._idx = {c.identifiant.lower(): c for c in self.comptes}
        return self._idx

    def par_categorie(self) -> dict[str, dict[str, int]]:
        ref = self.reference()
        resultat: dict[str, dict[str, int]] = {}
        for c in self.comptes:
            ligne = resultat.setdefault(c.categorie, {"total": 0, "actifs": 0, "desactives": 0,
                                                     "expires": 0, "inconnus": 0})
            ligne["total"] += 1
            if c.statut == "desactive":
                ligne["desactives"] += 1
            elif c.expire_le(ref):
                ligne["expires"] += 1
            elif c.statut == "actif":
                ligne["actifs"] += 1
            else:
                ligne["inconnus"] += 1
        return resultat

    def groupes_de_constats(self, famille: str) -> list[dict]:
        """Constats regroupés par type, triés par gravité puis par nombre."""
        groupes: dict[str, list[Constat]] = {}
        for k in self.constats:
            if k.famille == famille:
                groupes.setdefault(k.code, []).append(k)
        sortie = []
        for code, liste in groupes.items():
            fam, grav, titre, explication = TYPES[code]
            sortie.append({"code": code, "gravite": grav, "libelle_gravite": LIBELLE_GRAVITE[grav],
                           "titre": titre,
                           "explication": explication.format(seuil=self.parametres.get("seuil_dormance")),
                           "constats": sorted(liste, key=lambda k: k.identifiant.lower())})
        return sorted(sortie, key=lambda g: (ORDRE_GRAVITE[g["gravite"]], -len(g["constats"])))

    def nb(self, famille: str) -> int:
        return sum(1 for k in self.constats if k.famille == famille)

    def comptes_concernes(self, famille: str) -> int:
        return len({k.identifiant.lower() for k in self.constats if k.famille == famille})

    # --------------------------------------------------------------- JSON

    def en_dict(self) -> dict:
        return {"date_reference": self.date_reference,
                "comptes": [asdict(c) for c in self.comptes],
                "constats": [asdict(k) for k in self.constats],
                "champs_disponibles": self.champs_disponibles,
                "regles_non_evaluees": self.regles_non_evaluees,
                "conflits": self.conflits,
                "parametres": self.parametres}

    @classmethod
    def depuis_dict(cls, d: dict) -> "Revue":
        return cls(d["date_reference"], [Compte(**c) for c in d["comptes"]],
                   [Constat(**k) for k in d["constats"]], d["champs_disponibles"],
                   d["regles_non_evaluees"], d["conflits"], d["parametres"])


# ======================================================================= calcul

def _iso(valeur: str) -> tuple[str | None, bool]:
    """(date ISO ou None, vrai si la valeur signifie « pas de date »)."""
    statut, d = analyser_date(valeur)
    if statut == "valide":
        return d.isoformat(timespec="seconds"), False
    return None, statut in ("jamais", "vide")


def consolider(lectures: list[ResultatLecture], regles: Regles) -> tuple[list[Compte], set[str], list[str]]:
    """Fusionne les exports en une fiche par compte.

    Un même compte peut figurer dans plusieurs rapports ADAudit Plus (liste des
    utilisateurs, comptes créés…) : chaque rapport complète la fiche. En cas de
    valeurs différentes, la première rencontrée est gardée et le conflit est noté.
    """
    fiches: dict[str, dict] = {}
    champs_vus: set[str] = set()
    conflits: list[str] = []

    for lecture in lectures:
        col_id = lecture.colonne_du_champ("identifiant")
        if not col_id:
            continue
        colonnes = {champ: lecture.colonne_du_champ(champ) for champ in CHAMPS_COMPTE}
        colonnes = {k: v for k, v in colonnes.items() if v}
        champs_vus.update(colonnes)
        for _, ligne in lecture.donnees.iterrows():
            ident = ligne[col_id].strip()
            if not ident:
                continue
            fiche = fiches.setdefault(ident.lower(), {"identifiant": ident, "sources": [], "connus": set()})
            if lecture.nom_fichier not in fiche["sources"]:
                fiche["sources"].append(lecture.nom_fichier)
            fiche["connus"].update(colonnes)
            for champ, col in colonnes.items():
                valeur = str(ligne[col]).strip()
                if champ in CHAMPS_DATE:
                    iso, sans = _iso(valeur)
                    if champ == "date_expiration" and sans and "date_expiration" not in fiche:
                        fiche["sans_expiration"] = True
                    valeur = iso or ""
                if not valeur:
                    continue
                if champ not in fiche:
                    fiche[champ] = valeur
                    if champ == "date_expiration":
                        fiche["sans_expiration"] = False
                elif fiche[champ] != valeur and champ in CHAMPS_DATE + ("statut",):
                    conflits.append(f"{ident} : « {champ} » vaut {fiche[champ]} dans un fichier "
                                    f"et {valeur} dans {lecture.nom_fichier} (première valeur gardée).")

    comptes = []
    for fiche in fiches.values():
        classe = regles.nomenclature.classer(fiche["identifiant"])
        comptes.append(Compte(
            identifiant=fiche["identifiant"], categorie=classe.categorie, conforme=classe.conforme,
            motif_nomenclature=classe.motif,
            nom_affiche=fiche.get("nom_affiche", ""), upn=fiche.get("upn", ""),
            statut_brut=fiche.get("statut", ""), statut=regles.statut(fiche.get("statut", "")),
            date_creation=fiche.get("date_creation"), date_expiration=fiche.get("date_expiration"),
            sans_expiration=bool(fiche.get("sans_expiration")) and not fiche.get("date_expiration"),
            derniere_connexion=fiche.get("derniere_connexion"), operateur=fiche.get("operateur", ""),
            groupes=fiche.get("groupes", ""), mot_de_passe_jamais_expire=fiche.get("mot_de_passe_jamais_expire", ""),
            sources=fiche["sources"], champs_connus=sorted(fiche["connus"])))
    comptes.sort(key=lambda c: (c.categorie, c.identifiant.lower()))
    return comptes, champs_vus, conflits


def _jours(depuis: datetime, jusqu_a: datetime) -> int:
    return (jusqu_a - depuis).days


def _fr(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "non renseignée"


def detecter(comptes: list[Compte], champs: set[str], regles: Regles,
             reference: datetime) -> tuple[list[Constat], list[str]]:
    constats: list[Constat] = []
    non_evaluees: list[str] = []
    a_statut = "statut" in champs
    a_connexion = "derniere_connexion" in champs
    a_expiration = "date_expiration" in champs
    a_creation = "date_creation" in champs
    seuil = regles.seuil_dormance

    if not a_connexion:
        non_evaluees.append("Comptes dormants et connexions après expiration : aucune colonne "
                            "« dernière connexion » dans les exports.")
    if not a_expiration:
        non_evaluees.append("Comptes expirés et contradictions nom / expiration : aucune colonne "
                            "« date d'expiration » dans les exports.")
    if not a_statut:
        non_evaluees.append("Statut des comptes absent : tous les comptes sont considérés comme actifs "
                            "pour la dormance, et les comptes expirés non désactivés ne sont pas recherchés.")
    if a_connexion and not a_creation and regles.inclure_jamais_connectes:
        non_evaluees.append("Comptes jamais utilisés : aucune colonne « date de création » pour savoir "
                            "depuis quand ils existent.")

    nomenclature = regles.nomenclature
    for c in comptes:
        connus = set(c.champs_connus)
        temporaire = nomenclature.est_temporaire(c.categorie)
        expire = c.expire_le(reference)
        statut_connu = "statut" in connus
        actif = c.statut == "actif" or (c.statut == "inconnu" and not statut_connu)
        eligible_dormance = actif or (c.statut == "desactive" and not regles.dormance_exclut_desactives)

        # -- Nomenclature
        if not c.conforme:
            constats.append(Constat("nomenclature", c.identifiant, c.motif_nomenclature))

        # -- Contradictions nom / date d'expiration (notifications)
        if "date_expiration" in connus:
            if temporaire and not c.date_expiration:
                constats.append(Constat("temporaire_sans_expiration", c.identifiant,
                                        f"{nomenclature.libelle(c.categorie)} sans date d'expiration."))
            if not temporaire and c.date_expiration and c.conforme:
                constats.append(Constat("employe_avec_expiration", c.identifiant,
                                        f"Expiration au {_fr(c.date_expiration)}."))

        # -- Connexion après la date d'expiration
        connexion = c.date("derniere_connexion")
        expiration = c.date("date_expiration")
        if connexion and expiration and connexion > expiration and expire:
            constats.append(Constat("connexion_apres_expiration", c.identifiant,
                                    f"Expiré le {_fr(c.date_expiration)}, dernière connexion le "
                                    f"{_fr(c.derniere_connexion)}."))

        # -- Expirés restés actifs (nettoyage)
        if regles.signaler_expires_non_desactives and statut_connu and expire and c.statut == "actif":
            constats.append(Constat("expire_non_desactive", c.identifiant,
                                    f"Expiré depuis le {_fr(c.date_expiration)} "
                                    f"({_jours(expiration, reference)} jours)."))

        # -- Dormance
        if "derniere_connexion" not in connus or not eligible_dormance:
            continue
        if expire and regles.dormance_exclut_expires:
            continue
        if connexion:
            jours = _jours(connexion, reference)
            if jours > seuil:
                constats.append(Constat("dormant", c.identifiant,
                                        f"Dernière connexion le {_fr(c.derniere_connexion)}, il y a {jours} jours."))
        elif regles.inclure_jamais_connectes and c.date_creation:
            anciennete = _jours(c.date("date_creation"), reference)
            if anciennete > seuil:
                constats.append(Constat("jamais_connecte", c.identifiant,
                                        f"Créé le {_fr(c.date_creation)}, il y a {anciennete} jours, "
                                        "aucune connexion enregistrée."))

    return constats, non_evaluees


def reference_de(date_campagne: str) -> datetime:
    """La date de campagne, en fin de journée : référence de tous les calculs de durée."""
    return datetime.combine(date.fromisoformat(date_campagne), time(23, 59, 59))


def analyser(lectures: list[ResultatLecture], regles: Regles, date_campagne: str) -> Revue:
    reference = reference_de(date_campagne)
    comptes, champs, conflits = consolider(lectures, regles)
    constats, non_evaluees = detecter(comptes, champs, regles, reference)
    parametres = {
        "seuil_dormance": regles.seuil_dormance,
        "inclure_jamais_connectes": regles.inclure_jamais_connectes,
        "dormance_exclut_desactives": regles.dormance_exclut_desactives,
        "dormance_exclut_expires": regles.dormance_exclut_expires,
        "signaler_expires_non_desactives": regles.signaler_expires_non_desactives,
        "categories": {cle: {"libelle": c.libelle, "libelle_pluriel": c.libelle_pluriel,
                             "prefixe": c.prefixe, "temporaire": c.temporaire}
                       for cle, c in regles.nomenclature.categories.items()},
        "categorie_par_defaut": regles.nomenclature.defaut,
    }
    return Revue(reference.isoformat(timespec="seconds"), comptes, constats, sorted(champs),
                 non_evaluees, conflits[:200], parametres)
