"""Tests de la lecture et du contrôle qualité, uniquement sur données factices.

    python -m pytest -q
"""

import io
import sys
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from ingestion.lecteur import CorrespondanceColonnes, ErreurLecture, lire_csv, lire_xlsx  # noqa: E402
from ingestion.qualite import analyser_date, controler  # noqa: E402

MAINTENANT = datetime(2026, 10, 22)


@pytest.fixture(scope="module")
def corr():
    return CorrespondanceColonnes.depuis_fichier(RACINE / "config" / "mapping_colonnes.json")


def titres(constats):
    return " | ".join(c.titre for c in constats)


def test_csv_cp1252_point_virgule_avec_titre(corr):
    texte = ("Rapport : tous les utilisateurs\nGénéré le 22/10/2026\n\n"
             "SAM Account Name;Display Name;Created Time;Account Expires\n"
             "stagiaire_Ngono;Alice Ngono;01/09/2026 08:00:00;31/01/2027\n"
             "jmbarga;Jean Mbarga;12/03/2019;Never\n")
    res = lire_csv(texte.encode("cp1252"), "export.csv", corr)
    assert res.encodage == "cp1252"
    assert res.separateur == ";"
    assert res.ligne_entete == 4
    assert res.nb_lignes == 2
    assert res.correspondance["SAM Account Name"] == "identifiant"
    assert res.correspondance["Account Expires"] == "date_expiration"
    assert res.donnees["__ligne"].tolist() == [5, 6]  # numéros de ligne réels


def test_csv_virgule_utf8(corr):
    texte = "﻿sAMAccountName,whenCreated\nabc,2026-01-05\n"
    res = lire_csv(texte.encode("utf-8"), "e.csv", corr)
    assert res.separateur == ","
    assert res.correspondance == {"sAMAccountName": "identifiant", "whenCreated": "date_creation"}


def test_identifiant_absent_est_bloquant(corr):
    res = lire_csv(b"Col A,Col B\n1,2\n", "x.csv", corr)
    assert res.champs_obligatoires_absents == ["identifiant"]
    niveaux = [c.niveau for c in controler(res, corr, MAINTENANT)]
    assert "erreur" in niveaux


def test_constats_qualite(corr):
    texte = ("SAM Account Name;Created Time;Account Expires;Last Logon Time\n"
             "a;01/01/2026;Never;01/10/2026\n"
             "A;01/01/2026;Never;01/10/2026\n"        # doublon (casse différente)
             ";01/01/2026;Never;01/10/2026\n"         # identifiant vide
             "b;31/02/2026;01/12/2026;\n"             # date impossible
             "c;01/06/2026;01/01/2026;15/12/2026\n")  # expire avant création + connexion future
    res = lire_csv(texte.encode("utf-8"), "q.csv", corr)
    t = titres(controler(res, corr, MAINTENANT))
    assert "1 identifiant(s) présent(s) plusieurs fois" in t
    assert "1 ligne(s) sans identifiant" in t
    assert "1 date(s) illisible(s) dans « Date de création »" in t
    assert "expiré(s) avant leur création" in t
    assert "date(s) dans le futur dans « Dernière connexion »" in t
    assert "marqué(s) « jamais »" in t


def test_xlsx_choisit_la_bonne_feuille(corr):
    wb = Workbook()
    wb.active.title = "Paramètres"
    wb.active.append(["Période", "Tout"])
    ws = wb.create_sheet("Comptes")
    ws.append(["Titre du rapport"])
    ws.append(["Created User", "Caller User Name", "Time"])
    ws.append(["stagiaire_Fotso", "adm_factice1", datetime(2026, 9, 1)])
    flux = io.BytesIO()
    wb.save(flux)
    res = lire_xlsx(flux.getvalue(), "c.xlsx", corr)
    assert res.feuille == "Comptes"
    assert res.ligne_entete == 2
    assert res.correspondance["Caller User Name"] == "operateur"
    assert res.donnees.iloc[0]["Time"] == "2026-09-01"


def test_faux_xlsx_refuse(corr):
    with pytest.raises(ErreurLecture):
        lire_xlsx(b"pas un classeur", "faux.xlsx", corr)


@pytest.mark.parametrize("valeur,statut", [
    ("", "vide"), ("-", "vide"), ("Never", "jamais"), ("Jamais", "jamais"),
    ("22/10/2026", "valide"), ("22/10/2026 14:05:00", "valide"), ("2026-10-22", "valide"),
    ("Oct 22, 2026 02:05:00 PM", "valide"), ("31/02/2026", "invalide"), ("demain", "invalide"),
])
def test_analyser_date(valeur, statut):
    assert analyser_date(valeur)[0] == statut


def test_application_bout_en_bout(tmp_path):
    from config import Config
    from app import creer_app

    class ConfigTest(Config):
        DATA_DIR = tmp_path
        CAMPAGNES_DIR = tmp_path / "campagnes"
        JOURNAL = tmp_path / "journal.log"
        TESTING = True

    app = creer_app(ConfigTest)
    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    jeton = page.get_data(as_text=True).split('name="csrf" value="')[1].split('"')[0]

    # Sans jeton CSRF : refusé
    assert client.post("/campagnes", data={}).status_code == 400

    csv_ = "SAM Account Name;Created Time\nstagiaire_Test;01/10/2026\n".encode("utf-8")
    rep = client.post("/campagnes", content_type="multipart/form-data", data={
        "csrf": jeton, "nom": "Test <script>", "date_campagne": "2026-10-22",
        "fichiers": [(io.BytesIO(csv_), "../../etc/export.csv")],
    })
    assert rep.status_code == 302
    page = client.get(rep.headers["Location"]).get_data(as_text=True)
    assert "Test &lt;script&gt;" in page     # échappement HTML
    assert "stagiaire_Test" in page
    # Le nom d'origine n'est jamais utilisé comme chemin
    fichiers = list((tmp_path / "campagnes").glob("*/fichiers/*"))
    assert [f.name for f in fichiers] == ["01.csv"]

    # Extension refusée
    rep = client.post("/campagnes", content_type="multipart/form-data", data={
        "csrf": jeton, "fichiers": [(io.BytesIO(b"x"), "script.exe")]}, follow_redirects=True)
    assert "Format non pris en charge" in rep.get_data(as_text=True)

    # Rapport PDF de la campagne
    cid = [d.name for d in (tmp_path / "campagnes").iterdir()][0]
    pdf = client.get(f"/campagnes/{cid}/rapport.pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert "attachment" in pdf.headers["Content-Disposition"]

    # Recalcul : protégé par le jeton CSRF
    assert client.post(f"/campagnes/{cid}/recalculer").status_code == 400
    assert client.post(f"/campagnes/{cid}/recalculer", data={"csrf": jeton}).status_code == 302

    assert client.get("/campagnes/../../etc/passwd").status_code == 404
    assert client.get("/campagnes/pasunid").status_code == 404
