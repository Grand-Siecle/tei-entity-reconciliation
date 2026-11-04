# Kit du relecteur

Tout ce dont un relecteur a besoin pour vérifier les entités du corpus dans OpenRefine, sans aucune compétence technique.

| Fichier | À quoi il sert |
|---|---|
| [`guide_relecteur_openrefine.pdf`](guide_relecteur_openrefine.pdf) | **Le guide pas à pas**, avec captures d'écran. Commencez par là. (Source : [`guide_relecteur_openrefine.md`](guide_relecteur_openrefine.md)) |
| [`recettes/debut_de_lot.json`](recettes/debut_de_lot.json) | Recette à coller dans OpenRefine **au début** d'un lot : prépare la colonne `label_corrige` et transforme les propositions en liens Wikidata. |
| [`recettes/fin_de_lot.json`](recettes/fin_de_lot.json) | Recette à coller **à la fin** d'un lot : calcule les colonnes `qid_final`, `supprimer`, `doute` que le programme lira. |
| [`memo_expressions.md`](memo_expressions.md) | Mémo des filtres, facettes et expressions utiles (GREL, expressions régulières). |
| [`exemple/`](exemple/) | Un petit lot d'entraînement produit à partir des documents d'exemple du dépôt, pour s'exercer avant le premier vrai lot. |
| `img/` | Les captures d'écran du guide. |

## Comment récupérer les fichiers

- Pour lire le guide : cliquez sur le PDF ci-dessus, puis sur le bouton **Download** (ou **Télécharger**) en haut à droite de la page.
- Pour une recette : cliquez sur le fichier, puis sur **Raw** ; sélectionnez tout (Ctrl+A), copiez (Ctrl+C) et collez dans OpenRefine (**Défaire / Refaire → Appliquer…**).
- Pour tout le dossier d'un coup : en haut de la page du dépôt, **Code → Download ZIP**, puis ouvrez le sous-dossier `relecteurs/`.

## Le cycle, en bref

1. Le responsable technique vous envoie un lot (`person_lot01.csv` par exemple).
2. Vous l'ouvrez dans OpenRefine, vous appliquez la recette de début, vous relisez, vous appliquez la recette de fin, vous exportez en CSV.
3. Vous déposez le fichier exporté dans le dossier partagé, renommé avec votre prénom.
4. Le responsable importe vos décisions ; au lot suivant, les lignes déjà traitées sont marquées `inchangé`, les nouvelles `nouveau`.

Les règles de décision (quand accepter, corriger, rejeter, fusionner, supprimer) sont au chapitre 10 du guide.

## Pour le responsable technique

Le reste du dépôt documente la chaîne complète (extraction, réconciliation, registres, lots, import) : voir le [README principal](../README.md) et [`docs/workflow.fr.md`](../docs/workflow.fr.md). Pour régénérer le PDF à partir du Markdown : `python3 relecteurs/build_pdf.py` (nécessite le module Python `markdown` et un Chromium headless, voir l'en-tête du script).
