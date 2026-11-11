"""Rapport PDF de revue des comptes Active Directory.

Construit avec ReportLab (polices Helvetica intégrées au format PDF : aucun
fichier de police à installer sur le poste). Tout texte venant des exports est
échappé avant d'entrer dans un paragraphe : ReportLab interprète une forme de
balisage, un identifiant contenant « < » ne doit pas casser la mise en page.
"""

from __future__ import annotations

import io
from datetime import datetime
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from analyse.revue import LIBELLE_STATUT, Revue

# Palette alignée sur l'interface (approximations de l'identité BICEC, voir style.css)
ORANGE = colors.HexColor("#EE7D00")
ORANGE_PALE = colors.HexColor("#FFF1E0")
MARRON = colors.HexColor("#3A2214")
MARRON_MOYEN = colors.HexColor("#6A4630")
GRIS_TEXTE = colors.HexColor("#65564B")
LIGNE = colors.HexColor("#E1DCD7")
FOND_TETE = colors.HexColor("#F6F3F0")
COULEUR_GRAVITE = {"elevee": colors.HexColor("#B42318"), "moyenne": colors.HexColor("#B85F00"),
                   "faible": colors.HexColor("#5B6B78"), "": colors.HexColor("#3A5670")}

LARGEUR, HAUTEUR = A4
MARGE = 18 * mm
UTILE = LARGEUR - 2 * MARGE


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=colors.black)
    return {
        "base": base,
        "petit": ParagraphStyle("petit", parent=base, fontSize=8.5, leading=11.5, textColor=GRIS_TEXTE),
        "cellule": ParagraphStyle("cellule", parent=base, fontSize=8.3, leading=10.8),
        "cellule_g": ParagraphStyle("cellule_g", parent=base, fontName="Helvetica-Bold", fontSize=8.3, leading=10.8),
        "tete": ParagraphStyle("tete", parent=base, fontName="Helvetica-Bold", fontSize=8, leading=10,
                               textColor=MARRON_MOYEN),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19,
                             textColor=MARRON, spaceBefore=4, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                             textColor=MARRON, spaceBefore=12, spaceAfter=4),
        "titre": ParagraphStyle("titre", parent=base, fontName="Helvetica-Bold", fontSize=26, leading=31,
                                textColor=MARRON),
        "sous_titre": ParagraphStyle("sous_titre", parent=base, fontSize=13, leading=18, textColor=MARRON_MOYEN),
        "chiffre": ParagraphStyle("chiffre", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=23,
                                  textColor=MARRON),
        "chiffre_lib": ParagraphStyle("chiffre_lib", parent=base, fontSize=8.5, leading=11, textColor=GRIS_TEXTE),
        "droite": ParagraphStyle("droite", parent=base, alignment=TA_RIGHT),
    }


def _e(texte) -> str:
    return escape(str(texte if texte is not None else ""), quote=False)


def _date(iso: str | None, heure: bool = False) -> str:
    if not iso:
        return "-"
    d = datetime.fromisoformat(iso)
    return d.strftime("%d/%m/%Y %H:%M" if heure else "%d/%m/%Y")


def _tableau(entete: list[str], lignes: list[list], largeurs: list[float], st: dict,
             gras_premiere_colonne: bool = False, alignement_droite: set[int] = frozenset()) -> Table:
    def cellule(v, i, en_tete=False):
        if isinstance(v, Paragraph):
            return v
        style = st["tete"] if en_tete else (st["cellule_g"] if gras_premiere_colonne and i == 0 else st["cellule"])
        if i in alignement_droite:
            style = ParagraphStyle(f"{style.name}_d", parent=style, alignment=TA_RIGHT)
        return Paragraph(_e(v), style)

    donnees = [[cellule(h, i, True) for i, h in enumerate(entete)]]
    donnees += [[cellule(v, i) for i, v in enumerate(l)] for l in lignes]
    t = Table(donnees, colWidths=[UTILE * f for f in largeurs], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), FOND_TETE),
        ("LINEBELOW", (0, 0), (-1, 0), 1, LIGNE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LIGNE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


class _CanvasNumerote(rl_canvas.Canvas):
    """Canvas qui connaît le nombre total de pages (« Page 3 sur 12 »)."""

    def __init__(self, *args, entete: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []
        self._entete = entete

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for etat in self._pages:
            self.__dict__.update(etat)
            if self._pageNumber > 1:
                self._decor(total)
            super().showPage()
        super().save()

    def _decor(self, total: int):
        self.saveState()
        self.setStrokeColor(ORANGE)
        self.setLineWidth(1.2)
        self.line(MARGE, HAUTEUR - 12 * mm, LARGEUR - MARGE, HAUTEUR - 12 * mm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(GRIS_TEXTE)
        self.drawString(MARGE, HAUTEUR - 10 * mm, self._entete)
        self.drawRightString(LARGEUR - MARGE, HAUTEUR - 10 * mm, "BICEC · Diffusion restreinte")
        self.drawString(MARGE, 10 * mm, "Document généré automatiquement. Les constats sont à valider avant toute action.")
        self.drawRightString(LARGEUR - MARGE, 10 * mm, f"Page {self._pageNumber} sur {total}")
        self.restoreState()


# ============================================================================ sections

def _page_de_garde(analyse: dict, revue: Revue, st: dict, logo: Path | None) -> list:
    elements: list = []
    if logo and logo.suffix.lower() in (".png", ".jpg", ".jpeg"):
        img = Image(str(logo))
        ratio = img.imageWidth / img.imageHeight
        img.drawHeight = 18 * mm
        img.drawWidth = 18 * mm * ratio
        img.hAlign = "LEFT"
        elements.append(img)
    else:
        elements.append(Paragraph("BICEC", ParagraphStyle("marque", parent=st["base"], fontName="Helvetica-Bold",
                                                          fontSize=20, leading=24, textColor=MARRON)))
    elements.append(Spacer(0, 55 * mm))
    filet = Table([[""]], colWidths=[28 * mm], rowHeights=[2.2 * mm])
    filet.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ORANGE)]))
    filet.hAlign = "LEFT"
    elements += [filet, Spacer(0, 6 * mm),
                 Paragraph("Revue des comptes Active Directory", st["titre"]),
                 Spacer(0, 3 * mm),
                 Paragraph(_e(analyse["nom"]), st["sous_titre"]),
                 Spacer(0, 14 * mm)]

    infos = [
        ("Date de référence", _date(revue.date_reference)),
        ("Comptes analysés", str(len(revue.comptes))),
        ("Exports utilisés", str(analyse["synthese"]["lisibles"])),
        ("Destinataire", "DOQSI"),
        ("Rapport généré le", datetime.now().strftime("%d/%m/%Y à %H:%M")),
    ]
    t = Table([[Paragraph(_e(k), st["petit"]), Paragraph(_e(v), st["base"])] for k, v in infos],
              colWidths=[45 * mm, 90 * mm])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    t.hAlign = "LEFT"
    elements += [t, Spacer(0, 50 * mm),
                 Paragraph("<b>Diffusion restreinte.</b> Ce document contient des identifiants de comptes du "
                           "système d'information. Il est réservé aux personnes chargées de la revue des accès "
                           "et ne doit pas être transmis hors de la banque.", st["petit"]),
                 PageBreak()]
    return elements


def _synthese(revue: Revue, st: dict) -> list:
    n = revue.parametres["categories"]
    repartition = revue.par_categorie()
    ordre = [c for c in n if c in repartition] + [c for c in repartition if c not in n]
    anomalies = revue.groupes_de_constats("anomalie")
    notifications = revue.groupes_de_constats("notification")
    elevees = sum(len(g["constats"]) for g in anomalies if g["gravite"] == "elevee")

    detail_cat = ", ".join(f"{repartition[c]['total']} {n.get(c, {}).get('libelle_pluriel', c).lower()}"
                           for c in ordre)
    phrase = (f"La revue porte sur <b>{len(revue.comptes)} comptes</b> ({_e(detail_cat)}), "
              f"à la date du {_date(revue.date_reference)}. ")
    if anomalies:
        phrase += (f"Elle relève <b>{revue.nb('anomalie')} anomalie{'s' if revue.nb('anomalie') > 1 else ''}</b> "
                   f"concernant {revue.comptes_concernes('anomalie')} compte"
                   f"{'s' if revue.comptes_concernes('anomalie') > 1 else ''}")
        phrase += f", dont {elevees} de gravité élevée. " if elevees else ". "
    else:
        phrase += "Elle ne relève aucune anomalie. "
    if notifications:
        nn = revue.nb("notification")
        phrase += (f"{nn} notification{'s signalent' if nn > 1 else ' signale'} une contradiction entre "
                   "l'identifiant et la date d'expiration, à vérifier.")

    # Bandeau de chiffres clés
    total_actifs = sum(r["actifs"] for r in repartition.values())
    chiffres = [(len(revue.comptes), "comptes analysés"), (total_actifs, "comptes ouverts"),
                (revue.nb("anomalie"), "anomalies"), (revue.nb("notification"), "notifications à vérifier")]
    bandeau = Table([[Paragraph(str(v), st["chiffre"]) for v, _ in chiffres],
                     [Paragraph(_e(l), st["chiffre_lib"]) for _, l in chiffres]],
                    colWidths=[UTILE / 4] * 4)
    bandeau.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LIGNE), ("LINEBEFORE", (1, 0), (-1, -1), 0.6, LIGNE),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
    ]))

    lignes_rep = [[n.get(c, {}).get("libelle_pluriel", c), r["total"], r["actifs"], r["expires"],
                   r["desactives"], r["inconnus"]] for c, r in ((c, repartition[c]) for c in ordre)]
    tot = [sum(l[i] for l in lignes_rep) for i in range(1, 6)]
    lignes_rep.append(["Total", *tot])
    t_rep = _tableau(["Catégorie", "Total", "Ouverts", "Expirés non désactivés", "Désactivés", "Statut inconnu"],
                     lignes_rep, [0.24, 0.12, 0.12, 0.2, 0.15, 0.17], st, gras_premiere_colonne=True,
                     alignement_droite={1, 2, 3, 4, 5})
    t_rep.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.9, MARRON_MOYEN)]))

    lignes_k = []
    for g in anomalies + notifications:
        p = Paragraph(f'<font color="{COULEUR_GRAVITE[g["gravite"]].hexval().replace("0x", "#")}">'
                      f'<b>{_e(g["libelle_gravite"])}</b></font>', st["cellule"])
        lignes_k.append([g["titre"], p, len(g["constats"])])
    elements = [Paragraph("1. Synthèse", st["h1"]), Paragraph(phrase, st["base"]), Spacer(0, 5 * mm), bandeau,
                Paragraph("Répartition des comptes", st["h2"]), t_rep, Spacer(0, 1.5 * mm),
                Paragraph("Ouverts : actifs et non expirés. Expirés non désactivés : la date d'expiration est "
                          "passée (l'AD refuse la connexion) mais le compte n'a pas été désactivé.", st["petit"])]
    if lignes_k:
        elements += [Paragraph("Constats par type", st["h2"]),
                     _tableau(["Type de constat", "Gravité", "Comptes"], lignes_k, [0.62, 0.2, 0.18], st,
                              alignement_droite={2})]
    return elements


def _actions(revue: Revue, st: dict) -> list:
    propositions = {
        "connexion_apres_expiration": "Vérifier l'historique de la date d'expiration et l'origine des connexions "
                                      "dans ADAudit Plus.",
        "dormant": "Faire confirmer le besoin par le responsable de chaque titulaire, puis désactiver les comptes "
                   "sans justification.",
        "jamais_connecte": "Vérifier que le titulaire a bien pris ses fonctions ; sinon désactiver le compte.",
        "nomenclature": "Renommer les comptes selon la convention, après accord de l'administrateur.",
        "expire_non_desactive": "Désactiver puis supprimer les comptes selon la procédure de sortie.",
        "temporaire_sans_expiration": "Renseigner la date de fin de mission ou de stage.",
        "employe_avec_expiration": "Confirmer la catégorie du titulaire (CDD, stagiaire ou prestataire mal nommé).",
    }
    lignes = [[g["titre"], len(g["constats"]), propositions.get(g["code"], "")]
              for g in revue.groupes_de_constats("anomalie") + revue.groupes_de_constats("notification")]
    if not lignes:
        return []
    return [Paragraph("Actions proposées", st["h2"]),
            _tableau(["Constat", "Comptes", "Action proposée"], lignes, [0.32, 0.1, 0.58], st,
                     alignement_droite={1})]


def _section_constats(revue: Revue, famille: str, numero: int, titre: str, vide: str, st: dict) -> list:
    groupes = revue.groupes_de_constats(famille)
    cats = revue.parametres["categories"]
    elements = [CondPageBreak(60 * mm), Paragraph(f"{numero}. {titre}", st["h1"])]
    if not groupes:
        return elements + [Paragraph(vide, st["base"])]
    for g in groupes:
        lignes = []
        for k in g["constats"]:
            c = revue.compte(k.identifiant)
            lignes.append([k.identifiant, cats.get(c.categorie, {}).get("libelle", c.categorie) if c else "-",
                           LIBELLE_STATUT.get(c.statut, "-") if c else "-", k.detail])
        couleur = COULEUR_GRAVITE[g["gravite"]].hexval().replace("0x", "#")
        etiquette = f' <font color="{couleur}" size="9">Gravité {_e(g["libelle_gravite"].lower())}</font>' \
            if famille == "anomalie" else ""
        elements += [KeepTogether([
            Paragraph(f'{_e(g["titre"])} ({len(g["constats"])}){etiquette}', st["h2"]),
            Paragraph(_e(g["explication"]), st["petit"]), Spacer(0, 2 * mm)]),
            _tableau(["Identifiant", "Catégorie", "Statut", "Détail"], lignes, [0.25, 0.14, 0.13, 0.48], st,
                     gras_premiere_colonne=True)]
    return elements


def _comptes_temporaires(revue: Revue, numero: int, st: dict) -> list:
    ref = revue.reference()
    cats = revue.parametres["categories"]
    elements = [PageBreak(), Paragraph(f"{numero}. État des comptes temporaires", st["h1"]),
                Paragraph("Comptes identifiés comme temporaires par leur préfixe, avec les champs du suivi "
                          "habituel de la DOQSI. « En prolongation » n'est pas encore déterminé : il le sera par "
                          "comparaison des dates d'expiration entre deux campagnes (module Historique).", st["petit"])]
    for cle, c in cats.items():
        if not c.get("temporaire"):
            continue
        comptes = [x for x in revue.comptes if x.categorie == cle]
        elements.append(Paragraph(f"{_e(c['libelle_pluriel'])} ({len(comptes)})", st["h2"]))
        if not comptes:
            elements.append(Paragraph("Aucun compte dans cette catégorie.", st["base"]))
            continue
        lignes = [[x.identifiant, LIBELLE_STATUT.get(x.statut, "-"), _date(x.date_creation),
                   _date(x.date_expiration) if x.date_expiration else "Aucune",
                   x.operateur or "-", x.etat(ref)] for x in comptes]
        elements.append(_tableau(["Identifiant", "Statut AD", "Création", "Expiration", "Opérateur", "État"],
                                 lignes, [0.27, 0.13, 0.13, 0.13, 0.19, 0.15], st, gras_premiere_colonne=True))
    return elements


def _qualite(analyse: dict, revue: Revue, numero: int, st: dict) -> list:
    lignes = []
    for f in analyse["fichiers"]:
        bloquants = sum(1 for k in f["constats"] if k["niveau"] == "erreur")
        alertes = sum(1 for k in f["constats"] if k["niveau"] == "alerte")
        lignes.append([f["nom"], f["lignes"] if f["lisible"] else "Illisible", bloquants, alertes])
    elements = [CondPageBreak(70 * mm), Paragraph(f"{numero}. Qualité des données", st["h1"]),
                _tableau(["Export", "Lignes", "Points bloquants", "À corriger"], lignes,
                         [0.52, 0.14, 0.17, 0.17], st, alignement_droite={1, 2, 3})]
    remarques = []
    for f in analyse["fichiers"]:
        for k in f["constats"]:
            if k["niveau"] in ("erreur", "alerte"):
                remarques.append(f"{f['nom']} : {k['titre']}.")
    if remarques:
        elements += [Paragraph("Points relevés par le contrôle qualité", st["h2"])]
        elements += [Paragraph("• " + _e(r), st["cellule"]) for r in remarques[:40]]
    if revue.regles_non_evaluees:
        elements += [Paragraph("Règles non évaluées", st["h2"])]
        elements += [Paragraph("• " + _e(r), st["cellule"]) for r in revue.regles_non_evaluees]
    if revue.conflits:
        elements += [Paragraph(f"Valeurs différentes d'un export à l'autre ({len(revue.conflits)})", st["h2"])]
        elements += [Paragraph("• " + _e(r), st["cellule"]) for r in revue.conflits[:30]]
    return elements


def _methode(revue: Revue, numero: int, st: dict) -> list:
    p = revue.parametres
    noms = []
    for cle, c in p["categories"].items():
        regle = f"identifiant commençant par « {c['prefixe']} »" if c["prefixe"] else \
            "identifiant sans préfixe (catégorie par défaut)"
        noms.append([c["libelle"], regle, "Oui" if c["temporaire"] else "Non"])
    regles = [
        f"Compte dormant : compte ouvert dont la dernière connexion date de plus de {p['seuil_dormance']} jours "
        "avant la date de référence"
        + (" ; les comptes désactivés" if p["dormance_exclut_desactives"] else "")
        + (" et expirés en sont exclus." if p["dormance_exclut_expires"] else "."),
        "Compte jamais utilisé : compte ouvert, sans connexion enregistrée, créé depuis plus de "
        f"{p['seuil_dormance']} jours." if p["inclure_jamais_connectes"] else "Comptes jamais utilisés : non recherchés.",
        "Connexion après expiration : dernière connexion postérieure à une date d'expiration déjà passée.",
        "Identifiant non conforme : préfixe incomplet, mal orthographié ou mal séparé (ressemblance avec un "
        "préfixe connu).",
        "Notifications : contradiction entre la catégorie déduite de l'identifiant et la présence ou l'absence "
        "d'une date d'expiration.",
    ]
    return [CondPageBreak(80 * mm), Paragraph(f"{numero}. Méthode et paramètres", st["h1"]),
            Paragraph(f"Date de référence de tous les calculs de durée : {_date(revue.date_reference)} "
                      "(date de la campagne). Sources : exports ADAudit Plus déposés pour cette campagne, "
                      "fusionnés par identifiant.", st["base"]),
            Paragraph("Nomenclature des identifiants", st["h2"]),
            _tableau(["Catégorie", "Règle", "Compte temporaire"], noms, [0.2, 0.58, 0.22], st,
                     gras_premiere_colonne=True),
            Paragraph("Règles appliquées", st["h2"]),
            *[Paragraph("• " + _e(r), st["cellule"]) for r in regles],
            Paragraph("Limites", st["h2"]),
            Paragraph("• Dernière connexion : à confirmer avec l'équipe quel attribut AD alimente ce champ dans "
                      "ADAudit Plus. Certains attributs de connexion ne sont mis à jour que périodiquement, ce qui "
                      "peut décaler la date de quelques jours.", st["cellule"]),
            Paragraph("• Les prolongations ne sont pas encore détectées : les demandes étant faites sur papier, "
                      "elles ne laissent pas de trace numérique.", st["cellule"])]


# ============================================================================ point d'entrée

def generer_pdf(analyse: dict, revue: Revue, logo: Path | None = None) -> bytes:
    st = _styles()
    flux = io.BytesIO()
    doc = SimpleDocTemplate(flux, pagesize=A4, leftMargin=MARGE, rightMargin=MARGE,
                            topMargin=20 * mm, bottomMargin=18 * mm,
                            title=f"Revue des comptes AD - {analyse['nom']}",
                            author="Outil de revue des comptes AD", subject="Revue des comptes Active Directory")
    histoire = []
    histoire += _page_de_garde(analyse, revue, st, logo)
    histoire += _synthese(revue, st)
    histoire += _actions(revue, st)
    histoire += _section_constats(revue, "anomalie", 2, "Anomalies détectées", "Aucune anomalie détectée.", st)
    histoire += _section_constats(revue, "notification", 3, "Notifications à vérifier",
                                  "Aucune contradiction relevée.", st)
    histoire += _comptes_temporaires(revue, 4, st)
    histoire += _qualite(analyse, revue, 5, st)
    histoire += _methode(revue, 6, st)

    entete = f"Revue des comptes Active Directory · {analyse['nom']}"
    if len(entete) > 95:
        entete = entete[:92] + "..."
    doc.build(histoire, canvasmaker=lambda *a, **k: _CanvasNumerote(*a, entete=entete, **k))
    return flux.getvalue()
