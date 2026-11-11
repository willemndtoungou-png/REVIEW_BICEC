# Revue des comptes Active Directory (BICEC, DOQSI)

Outil local d'automatisation de la revue des comptes AD à partir des exports d'ADAudit Plus.

**État :** dépôt des exports CSV, contrôle qualité, catégorisation, anomalies, notifications et rapport PDF
livrés. L'historique des campagnes (et la détection des prolongations) reste à développer.

## Lancer l'outil

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux : source .venv/bin/activate)
pip install -r requirements.txt
python donnees_factices/generer.py   # crée des exports factices pour tester
python app.py
```

Ouvrir ensuite <http://127.0.0.1:5000> et déposer les fichiers de `donnees_factices/`.

Tests : `python -m pytest -q`

## Organisation

| Chemin | Rôle |
|---|---|
| `app.py` | Application Flask : routes, sécurité, journal |
| `config.py` | Paramètres (tous surchargeables par variables d'environnement) |
| `config/mapping_colonnes.json` | Intitulés de colonnes reconnus pour chaque champ. **À compléter avec les vrais intitulés des exports.** |
| `ingestion/lecteur.py` | Lecture CSV/XLSX : encodage, séparateur, ligne d'en-tête, feuille, colonnes |
| `ingestion/qualite.py` | Contrôle qualité : identifiants vides ou en double, dates illisibles, incohérences |
| `config/regles.json` | Nomenclatures, seuil de dormance, options des règles. **Modifiable sans toucher au code.** |
| `analyse/regles.py` | Classe `Nomenclature` (préfixes paramétrables) et lecture du statut des comptes |
| `analyse/revue.py` | Fusion des exports par identifiant, catégorisation, anomalies et notifications |
| `rapport/pdf.py` | Rapport PDF (ReportLab) |
| `templates/`, `static/` | Interface |
| `static/img/` | Emplacement du logo officiel (voir `A_LIRE.txt`) |
| `donnees_factices/` | Générateur d'exports fictifs et cas d'erreur |
| `instance/` | Créé au lancement : campagnes déposées et `journal.log`. **Ne jamais versionner ni partager.** |

## Ce que fait le contrôle qualité

Pour chaque fichier : format, encodage et séparateur détectés, lignes de titre ignorées, feuille retenue
dans un classeur, correspondance des colonnes, puis les constats classés en trois niveaux.

- **Bloquant** : fichier illisible, colonne d'identifiant introuvable, fichier sans données.
- **À corriger** : identifiants vides ou en double (sans tenir compte des majuscules), dates illisibles,
  dates dans le futur, compte expiré avant sa création, date de création vide.
- **Information** : colonnes non exploitées, lignes identiques, valeurs « Never/Jamais » (aucune expiration).

Chaque constat donne les numéros de ligne du fichier d'origine.

## Catégorisation et règles

Nomenclatures confirmées par l'encadreur : `stagiaire_<nom>`, `prestataire_<nom>`, employé = nom sans
préfixe. Aucun autre type de compte.

| Constat | Famille | Règle |
|---|---|---|
| Connexion après expiration | Anomalie, gravité élevée | Dernière connexion postérieure à une date d'expiration déjà passée |
| Compte actif dormant | Anomalie, moyenne | Compte actif, non expiré, sans connexion depuis plus de 90 jours |
| Compte actif jamais utilisé | Anomalie, moyenne | Compte actif sans connexion, créé depuis plus de 90 jours |
| Identifiant non conforme | Anomalie, moyenne | Préfixe sans nom, mal orthographié (`stagiare_`), abrégé (`presta_`) ou mal séparé (`stagiaire-`) |
| Expiré non désactivé | Anomalie, faible | Date d'expiration passée mais statut « Enabled » (nettoyage : l'AD refuse déjà la connexion) |
| Temporaire sans expiration | Notification | Préfixe stagiaire/prestataire mais aucune date d'expiration |
| Sans préfixe avec expiration | Notification | Employé (sans préfixe) dont le compte a une date d'expiration |

Toutes les durées sont calculées à la **date de la campagne**. Un compte n'est jugé que sur les champs
présents dans les exports où il figure : un compte vu seulement dans le rapport des créations n'est
jamais déclaré « jamais connecté ». Après une modification de `regles.json`, le bouton « Recalculer »
réapplique les règles à une campagne existante.

## Rapport PDF

Page de garde, synthèse chiffrée et actions proposées, anomalies par type, notifications, état des
comptes temporaires (identifiant, statut, création, expiration, opérateur, ouvert/fermé), qualité des
données, méthode et paramètres. Le logo apparaît sur la page de garde s'il est fourni en PNG ou JPG
(`static/img/logo-bicec.png`) ; le SVG ne sert qu'à l'interface.

## Sécurité

- Écoute uniquement sur `127.0.0.1` : les données ne sortent pas du poste.
- Aucun secret dans le code : `REVUE_AD_SECRET_KEY` ou clé aléatoire à chaque démarrage.
- Jeton anti-CSRF sur tous les formulaires ; cookies `HttpOnly` et `SameSite=Strict`.
- En-têtes : CSP stricte (`default-src 'self'`, aucun script en ligne), `X-Frame-Options`, `nosniff`,
  `no-store`, `no-referrer`. Aucune ressource externe (polices, CDN) : fonctionne hors ligne.
- Fichiers : extensions `.csv`/`.xlsx` uniquement, signature vérifiée pour les `.xlsx`, taille et nombre
  limités, nom d'origine jamais utilisé comme chemin (enregistrés sous `01.csv`, `02.xlsx`…).
- Affichage : tout le contenu des fichiers est échappé (pas d'injection HTML).
- Journal `instance/journal.log` : actions et volumes, jamais le contenu des fichiers.
- Suppression d'une campagne : efface ses fichiers du poste.

- Rapport PDF : tout texte venant des exports est échappé avant mise en page.

À traiter dans les modules suivants : neutraliser les formules (`=`, `+`, `-`, `@`) si l'outil produit
un jour un export CSV/XLSX ; authentification si l'outil passe sur un serveur partagé ; durée de
conservation des campagnes.

## Paramètres (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `REVUE_AD_SECRET_KEY` | aléatoire | Clé de signature des sessions |
| `REVUE_AD_DATA_DIR` | `./instance` | Dossier des campagnes et du journal |
| `REVUE_AD_MAX_MO` | 25 | Taille maximale d'un envoi |
| `REVUE_AD_MAX_FICHIERS` | 20 | Nombre maximal de fichiers par campagne |
| `REVUE_AD_MAPPING` | `config/mapping_colonnes.json` | Table de correspondance des colonnes |
| `REVUE_AD_REGLES` | `config/regles.json` | Règles de la revue |
| `REVUE_AD_HTTPS` | 0 | Passer à 1 si servi en HTTPS |

## Hypothèses à confirmer

1. Intitulés des colonnes et format des dates des exports ADAudit Plus (alias du fichier de correspondance).
2. Dates à barres obliques lues en jour/mois/année.
3. Codes couleur : approximations de l'orange et du marron de l'identité BICEC/BCP.
4. Attribut AD derrière la « dernière connexion » d'ADAudit Plus.
5. Le modèle de rapport a été conçu par nous en l'absence d'exemple : à faire valider par la DOQSI.
