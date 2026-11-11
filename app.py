"""Outil de revue des comptes Active Directory — interface web locale.

Chaîne : dépôt des exports CSV d'ADAudit Plus -> contrôle qualité -> catégorisation
-> anomalies et notifications -> rapport PDF.

Lancement :  python app.py   puis ouvrir http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import uuid
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.exceptions import RequestEntityTooLarge

from analyse.regles import Regles
from analyse.revue import LIBELLE_STATUT, Revue
from analyse.revue import analyser as analyser_revue
from config import Config
from ingestion.lecteur import (COLONNE_LIGNE, CorrespondanceColonnes,
                               ErreurLecture, ResultatLecture, lire_fichier)
from ingestion.qualite import Constat, controler, identifiants
from rapport.pdf import generer_pdf

ID_CAMPAGNE = re.compile(r"^[0-9a-f]{32}$")

ETAPES = [
    {"cle": "depot", "titre": "Dépôt des exports", "aide": "CSV d'ADAudit Plus", "dispo": True},
    {"cle": "controle", "titre": "Contrôle qualité", "aide": "Colonnes, doublons, dates", "dispo": True},
    {"cle": "categorisation", "titre": "Catégorisation", "aide": "Employé, stagiaire, prestataire", "dispo": True},
    {"cle": "anomalies", "titre": "Anomalies", "aide": "Dormants, expirés, nomenclature", "dispo": True},
    {"cle": "rapport", "titre": "Rapport PDF", "aide": "État des lieux pour la DOQSI", "dispo": True},
    {"cle": "historique", "titre": "Historique", "aide": "Comparaison des campagnes", "dispo": False},
]


def creer_app(config: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)
    app.config["CAMPAGNES_DIR"].mkdir(parents=True, exist_ok=True)
    journal = _configurer_journal(app)
    correspondance = CorrespondanceColonnes.depuis_fichier(app.config["MAPPING_COLONNES"])

    def regles() -> Regles:
        # Relu à chaque campagne : une modification de regles.json s'applique sans redémarrer
        return Regles.depuis_fichier(app.config["REGLES"])

    # ------------------------------------------------------------------ sécurité

    def jeton_csrf() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def verifier_csrf() -> None:
        recu = request.form.get("csrf", "")
        attendu = session.get("csrf", "")
        if not attendu or not secrets.compare_digest(recu, attendu):
            journal.warning("csrf_refuse ip=%s chemin=%s", request.remote_addr, request.path)
            abort(400, "Le formulaire a expiré. Rechargez la page et recommencez.")

    @app.after_request
    def entetes_securite(reponse):
        reponse.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        reponse.headers["X-Content-Type-Options"] = "nosniff"
        reponse.headers["X-Frame-Options"] = "DENY"
        reponse.headers["Referrer-Policy"] = "no-referrer"
        reponse.headers["Cache-Control"] = "no-store"
        return reponse

    def logo(extensions=("svg", "png", "jpg")) -> Path | None:
        img = Path(app.static_folder) / "img"
        return next((p for ext in extensions for p in [img / f"logo-bicec.{ext}"] if p.exists()), None)

    @app.context_processor
    def contexte_commun():
        chemin = logo()
        return {"csrf_token": jeton_csrf, "logo": f"img/{chemin.name}" if chemin else None, "etapes": ETAPES}

    # ------------------------------------------------------------------ campagnes

    def dossier(cid: str) -> Path:
        if not ID_CAMPAGNE.match(cid):
            abort(404)
        chemin = app.config["CAMPAGNES_DIR"] / cid
        if not (chemin / "analyse.json").exists():
            abort(404)
        return chemin

    def lire_json(chemin: Path) -> dict:
        return json.loads(chemin.read_text(encoding="utf-8"))

    def ecrire_json(chemin: Path, donnees: dict) -> None:
        chemin.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")

    def lister_campagnes() -> list[dict]:
        resultats = []
        for f in app.config["CAMPAGNES_DIR"].glob("*/analyse.json"):
            try:
                a = lire_json(f)
            except (OSError, json.JSONDecodeError):
                continue
            s = a["synthese"]
            resultats.append({"id": a["id"], "nom": a["nom"], "date": a["date_campagne"],
                              "fichiers": len(a["fichiers"]), "comptes": s.get("comptes", s["comptes_uniques"]),
                              "anomalies": s.get("anomalies"), "erreurs": s["erreurs"], "cree_le": a["cree_le"]})
        return sorted(resultats, key=lambda c: c["cree_le"], reverse=True)

    def lire_depot(chemin: Path, nom: str) -> tuple[dict, ResultatLecture | None]:
        """Lit un fichier déposé : résumé sérialisable + lecture complète pour la revue."""
        try:
            res = lire_fichier(chemin, nom, correspondance)
        except ErreurLecture as e:
            return {"nom": nom, "stockage": chemin.name, "lisible": False,
                    "constats": [Constat("erreur", "Fichier illisible", str(e)).en_dict()],
                    "lignes": 0, "identifiants": []}, None
        constats = controler(res, correspondance)
        colonnes = [c for c in res.donnees.columns if c != COLONNE_LIGNE]
        apercu = res.donnees.head(app.config["LIGNES_APERCU"])
        return {
            "nom": nom, "stockage": chemin.name, "lisible": True, "format": res.format, "encodage": res.encodage,
            "separateur": {"\t": "tabulation", ";": "point-virgule", ",": "virgule", "|": "barre verticale"}
                          .get(res.separateur, res.separateur),
            "feuille": res.feuille, "ligne_entete": res.ligne_entete, "lignes": res.nb_lignes,
            "colonnes": colonnes,
            "correspondance": [{"colonne": c, "champ": res.correspondance.get(c),
                                "libelle": correspondance.libelle(res.correspondance[c])
                                if c in res.correspondance else None} for c in colonnes],
            "apercu": {"entete": ["Ligne", *colonnes],
                       "lignes": apercu[[COLONNE_LIGNE, *colonnes]].astype(str).values.tolist()},
            "avertissements": res.avertissements,
            "constats": [c.en_dict() for c in constats],
            "identifiants": sorted(identifiants(res)),
        }, res

    def calculer_revue(rep: Path, analyse: dict, lectures: list[ResultatLecture] | None = None) -> Revue:
        if lectures is None:  # campagne créée avant le module de revue : on relit les fichiers
            lectures = []
            for f in analyse["fichiers"]:
                if f.get("lisible") and f.get("stockage"):
                    try:
                        lectures.append(lire_fichier(rep / "fichiers" / f["stockage"], f["nom"], correspondance))
                    except ErreurLecture:
                        continue
        revue = analyser_revue(lectures, regles(), analyse["date_campagne"])
        ecrire_json(rep / "revue.json", revue.en_dict())
        return revue

    def charger(cid: str) -> tuple[Path, dict, Revue]:
        rep = dossier(cid)
        analyse = lire_json(rep / "analyse.json")
        if (rep / "revue.json").exists():
            revue = Revue.depuis_dict(lire_json(rep / "revue.json"))
        else:
            revue = calculer_revue(rep, analyse)
        return rep, analyse, revue

    # ------------------------------------------------------------------ routes

    @app.get("/")
    def accueil():
        return render_template("accueil.html", campagnes=lister_campagnes(),
                               aujourdhui=date.today().isoformat(),
                               max_mo=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
                               max_fichiers=app.config["MAX_FICHIERS"])

    @app.post("/campagnes")
    def creer_campagne():
        verifier_csrf()
        nom = (request.form.get("nom") or "").strip()[:120]
        date_campagne = request.form.get("date_campagne") or date.today().isoformat()
        try:
            date.fromisoformat(date_campagne)
        except ValueError:
            flash("La date de campagne n'est pas valide.", "erreur")
            return redirect(url_for("accueil"))
        fichiers = [f for f in request.files.getlist("fichiers") if f and f.filename]
        if not fichiers:
            flash("Ajoutez au moins un export CSV.", "erreur")
            return redirect(url_for("accueil"))
        if len(fichiers) > app.config["MAX_FICHIERS"]:
            flash(f"{len(fichiers)} fichiers envoyés : la limite est de {app.config['MAX_FICHIERS']}.", "erreur")
            return redirect(url_for("accueil"))
        refuses = [f.filename for f in fichiers
                   if Path(f.filename).suffix.lower() not in app.config["EXTENSIONS_AUTORISEES"]]
        if refuses:
            flash("Format non pris en charge : " + ", ".join(refuses[:5]) + ". Seuls .csv et .xlsx sont acceptés.",
                  "erreur")
            return redirect(url_for("accueil"))

        cid = uuid.uuid4().hex
        rep = app.config["CAMPAGNES_DIR"] / cid
        (rep / "fichiers").mkdir(parents=True)
        analyses, lectures = [], []
        for i, f in enumerate(fichiers, start=1):
            # Le nom d'origine n'est jamais utilisé comme chemin sur le disque
            nom_affiche = Path(f.filename).name[:150]
            chemin = rep / "fichiers" / f"{i:02d}{Path(nom_affiche).suffix.lower()}"
            f.save(chemin)
            resume, lecture = lire_depot(chemin, nom_affiche)
            resume["taille"] = chemin.stat().st_size
            analyses.append(resume)
            if lecture is not None:
                lectures.append(lecture)

        tous_ids = set().union(*(set(a["identifiants"]) for a in analyses))
        tous_constats = [c for a in analyses for c in a["constats"]]
        for a in analyses:
            a.pop("identifiants")
        donnees = {
            "id": cid, "nom": nom or f"Campagne du {date_campagne}", "date_campagne": date_campagne,
            "cree_le": datetime.now().isoformat(timespec="seconds"),
            "fichiers": analyses,
            "synthese": {
                "fichiers": len(analyses),
                "lisibles": sum(1 for a in analyses if a["lisible"]),
                "lignes": sum(a["lignes"] for a in analyses),
                "comptes_uniques": len(tous_ids),
                "erreurs": sum(1 for c in tous_constats if c["niveau"] == "erreur"),
                "alertes": sum(1 for c in tous_constats if c["niveau"] == "alerte"),
            },
        }
        revue = calculer_revue(rep, donnees, lectures)
        donnees["synthese"].update({"comptes": len(revue.comptes), "anomalies": revue.nb("anomalie"),
                                    "notifications": revue.nb("notification")})
        ecrire_json(rep / "analyse.json", donnees)
        journal.info("campagne_creee id=%s fichiers=%d lignes=%d comptes=%d anomalies=%d ip=%s", cid,
                     len(analyses), donnees["synthese"]["lignes"], len(revue.comptes), revue.nb("anomalie"),
                     request.remote_addr)
        return redirect(url_for("voir_campagne", cid=cid))

    @app.get("/campagnes/<cid>")
    def voir_campagne(cid: str):
        _, analyse, revue = charger(cid)
        categories = revue.parametres["categories"]
        repartition = revue.par_categorie()
        ordre = [c for c in categories if c in repartition]
        return render_template("campagne.html", c=analyse, revue=revue, categories=categories,
                               repartition=[(c, repartition[c]) for c in ordre],
                               anomalies=revue.groupes_de_constats("anomalie"),
                               notifications=revue.groupes_de_constats("notification"),
                               statuts=LIBELLE_STATUT)

    @app.post("/campagnes/<cid>/recalculer")
    def recalculer(cid: str):
        """Réapplique config/regles.json (après un changement de seuil ou de nomenclature)."""
        verifier_csrf()
        rep = dossier(cid)
        analyse = lire_json(rep / "analyse.json")
        revue = calculer_revue(rep, analyse)
        analyse["synthese"].update({"comptes": len(revue.comptes), "anomalies": revue.nb("anomalie"),
                                    "notifications": revue.nb("notification")})
        ecrire_json(rep / "analyse.json", analyse)
        journal.info("revue_recalculee id=%s ip=%s", cid, request.remote_addr)
        flash("La revue a été recalculée avec les règles actuelles.", "ok")
        return redirect(url_for("voir_campagne", cid=cid))

    @app.get("/campagnes/<cid>/rapport.pdf")
    def rapport_pdf(cid: str):
        _, analyse, revue = charger(cid)
        contenu = generer_pdf(analyse, revue, logo(("png", "jpg")))
        journal.info("rapport_genere id=%s comptes=%d ip=%s", cid, len(revue.comptes), request.remote_addr)
        nom = f"revue-comptes-AD_{analyse['date_campagne']}.pdf"
        return send_file(BytesIO(contenu), mimetype="application/pdf", as_attachment=True, download_name=nom)

    @app.post("/campagnes/<cid>/supprimer")
    def supprimer_campagne(cid: str):
        verifier_csrf()
        chemin = dossier(cid)
        shutil.rmtree(chemin)
        journal.info("campagne_supprimee id=%s ip=%s", cid, request.remote_addr)
        flash("La campagne et ses fichiers ont été supprimés de ce poste.", "ok")
        return redirect(url_for("accueil"))

    # ------------------------------------------------------------------ erreurs

    @app.errorhandler(RequestEntityTooLarge)
    def trop_gros(_e):
        flash(f"L'envoi dépasse {app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} Mo. "
              "Envoyez les fichiers en plusieurs campagnes ou augmentez REVUE_AD_MAX_MO.", "erreur")
        return redirect(url_for("accueil"))

    @app.errorhandler(400)
    @app.errorhandler(404)
    def erreur_http(e):
        return render_template("erreur.html", code=e.code, message=e.description), e.code

    return app


def _configurer_journal(app: Flask) -> logging.Logger:
    """Journal d'activité : qui a fait quoi, sans jamais écrire le contenu des fichiers."""
    journal = logging.getLogger("revue_ad")
    if not journal.handlers:
        app.config["JOURNAL"].parent.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(app.config["JOURNAL"], encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        journal.addHandler(h)
        journal.setLevel(logging.INFO)
    return journal


if __name__ == "__main__":
    # Écoute uniquement sur le poste local : les données ne sortent pas de la machine.
    creer_app().run(host="127.0.0.1", port=5000, debug=False)
