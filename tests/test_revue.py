"""Tests de la catégorisation, des anomalies, des notifications et du rapport PDF."""

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from analyse.regles import Regles  # noqa: E402
from analyse.revue import Revue, analyser  # noqa: E402
from ingestion.lecteur import CorrespondanceColonnes, lire_csv  # noqa: E402


@pytest.fixture(scope="module")
def corr():
    return CorrespondanceColonnes.depuis_fichier(RACINE / "config" / "mapping_colonnes.json")


@pytest.fixture(scope="module")
def regles():
    return Regles.depuis_fichier(RACINE / "config" / "regles.json")


@pytest.mark.parametrize("ident,categorie,conforme", [
    ("stagiaire_Ngono", "stagiaire", True),
    ("STAGIAIRE_Ngono", "stagiaire", True),
    ("prestataire_Fotso", "prestataire", True),
    ("ngono", "employe", True),
    ("jean.ngono", "employe", True),
    ("prestige", "employe", True),
    ("stagiaire_", "stagiaire", False),
    ("stagiare_Ngono", "stagiaire", False),
    ("stagiaire-Ngono", "stagiaire", False),
    ("presta_Fotso", "prestataire", False),
    ("prestataires_Fotso", "prestataire", False),  # « s » en trop : non conforme
])
def test_nomenclature(regles, ident, categorie, conforme):
    r = regles.nomenclature.classer(ident)
    assert (r.categorie, r.conforme) == (categorie, conforme), r.motif


@pytest.mark.parametrize("valeur,attendu", [
    ("Enabled", "actif"), ("Disabled", "desactive"), ("Désactivé", "desactive"), ("Actif", "actif"),
    ("", "inconnu"), ("Inconnu?", "inconnu"), ("Disabled, Locked", "desactive"),
])
def test_statut(regles, valeur, attendu):
    assert regles.statut(valeur) == attendu


UTILISATEURS = """SAM Account Name;Account Status;Created Time;Account Expires;Last Logon Time
ngono;Enabled;01/01/2024;Never;01/11/2026
mbarga;Enabled;01/01/2024;Never;01/05/2026
fotso;Disabled;01/01/2024;Never;01/01/2025
ebogo;Enabled;01/01/2025;Never;
stagiaire_Abena;Enabled;01/06/2026;01/10/2026;25/09/2026
stagiaire_Retour;Enabled;01/03/2026;01/09/2026;05/11/2026
stagiaire_SansFin;Enabled;01/10/2026;Never;05/11/2026
tchoumi;Enabled;01/10/2026;01/03/2027;05/11/2026
stagiare_Faute;Enabled;01/10/2026;01/03/2027;05/11/2026
"""

CREATIONS = """Created User,Caller User Name,Time
stagiaire_Abena,adm1,01/06/2026
nouveau,adm2,01/01/2025
"""


@pytest.fixture(scope="module")
def revue(corr, regles):
    a = lire_csv(UTILISATEURS.encode("utf-8"), "utilisateurs.csv", corr)
    b = lire_csv(CREATIONS.encode("utf-8"), "creations.csv", corr)
    return analyser([a, b], regles, "2026-11-10")


def codes(revue, ident):
    return sorted(k.code for k in revue.constats if k.identifiant == ident)


def test_fusion_des_exports(revue):
    assert len(revue.comptes) == 10
    abena = revue.compte("stagiaire_abena")
    assert abena.operateur == "adm1" and abena.statut == "actif"
    assert abena.sources == ["utilisateurs.csv", "creations.csv"]


def test_anomalies(revue):
    assert codes(revue, "ngono") == []
    assert codes(revue, "mbarga") == ["dormant"]                       # 193 jours
    assert codes(revue, "fotso") == []                                 # désactivé : exclu de la dormance
    assert codes(revue, "ebogo") == ["jamais_connecte"]
    assert codes(revue, "stagiaire_Abena") == ["expire_non_desactive"]  # expiré, encore Enabled
    assert codes(revue, "stagiaire_Retour") == ["connexion_apres_expiration", "expire_non_desactive"]
    assert codes(revue, "stagiare_Faute") == ["nomenclature"]


def test_notifications(revue):
    assert codes(revue, "stagiaire_SansFin") == ["temporaire_sans_expiration"]
    assert codes(revue, "tchoumi") == ["employe_avec_expiration"]


def test_pas_de_faux_positif_hors_export_utilisateurs(revue):
    # « nouveau » n'apparaît que dans le rapport des créations : aucune info de connexion
    # ni d'expiration, donc ni « jamais connecté » ni notification.
    assert codes(revue, "nouveau") == []


def test_repartition(revue):
    rep = revue.par_categorie()
    assert rep["employe"]["total"] == 6 and rep["employe"]["desactives"] == 1
    assert rep["stagiaire"]["expires"] == 2


def test_aller_retour_json(revue):
    copie = Revue.depuis_dict(revue.en_dict())
    assert copie.en_dict() == revue.en_dict()


def test_regle_non_evaluee_sans_colonne_connexion(corr, regles):
    a = lire_csv(b"SAM Account Name;Account Expires\nngono;Never\n", "a.csv", corr)
    r = analyser([a], regles, "2026-11-10")
    assert any("dormants" in x for x in r.regles_non_evaluees)
    assert not [k for k in r.constats if k.code in ("dormant", "jamais_connecte")]


def test_pdf_genere_et_echappe(corr, regles):
    from rapport.pdf import generer_pdf
    texte = UTILISATEURS + "x<b>&y;Enabled;01/01/2024;Never;01/11/2026\n"
    a = lire_csv(texte.encode("utf-8"), "u.csv", corr)
    r = analyser([a], regles, "2026-11-10")
    analyse = {"nom": "Campagne <test> & co", "synthese": {"lisibles": 1},
               "fichiers": [{"nom": "u.csv", "lignes": a.nb_lignes, "lisible": True, "constats": []}]}
    pdf = generer_pdf(analyse, r)
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000
