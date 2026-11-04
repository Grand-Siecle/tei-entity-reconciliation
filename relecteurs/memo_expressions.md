# Mémo : filtres, facettes et expressions utiles dans OpenRefine

Tout se lance depuis le **triangle** à gauche du nom d'une colonne. Les expressions ci-dessous sont en GREL, le langage d'OpenRefine ; copiez-les telles quelles dans la zone *Expression* des fenêtres **Facette personnalisée…**, **Transformer…** ou **Ajouter une colonne en fonction de cette colonne…**. Un aperçu du résultat s'affiche avant de valider.

## Filtres de texte (menu « Filtrer le texte »)

Une case apparaît à gauche. Tapez un mot : seules les lignes dont la colonne contient ce mot restent visibles. Cochez **expression régulière** pour les motifs ci-dessous.

| Motif | Trouve |
|---|---|
| `Lomen|Lomén` | les lignes contenant l'une ou l'autre graphie |
| `^S\.? ` | les libellés qui commencent par « S. » ou « S » (saints) |
| `[IVXLC]+$` | les libellés qui finissent par des chiffres romains |
| `\bde\b` | le mot « de » entier (pas « Descartes ») |
| `ſ` | la lettre s long, fréquente dans les textes anciens |
| `[0-9]` | les libellés qui contiennent un chiffre (souvent une erreur de lecture) |

## Facettes (menu « Facette »)

- **Facette textuelle** sur *confiance* : cliquez sur `medium` ou `low` pour relire d'abord les propositions peu sûres.
- **Facette textuelle** sur *statut* : cliquez sur `nouveau` ou `modifié` pour ne traiter que ce qui a changé depuis votre dernier lot.
- **Facette numérique** sur *mentions* ou *nb_docs* : glissez les curseurs pour ne garder que les entités fréquentes.
- Menu **Toutes → Facette → Facette par étoile** / **Facette par drapeau** : ne voir que vos étoiles ou vos drapeaux avant d'exporter.
- Sur *qid_propose*, **Réconcilier → Facettes → Par avis** : `matched` = une fiche est choisie, `(blank)` = aucune.

## Expressions GREL pour une facette personnalisée (menu « Facette → Facette personnalisée… »)

| Expression | Donne |
|---|---|
| `isBlank(value)` | vrai quand la cellule est vide |
| `value.contains("Saint")` | vrai quand le texte contient « Saint » |
| `value.toLowercase().contains("rome")` | idem sans tenir compte des majuscules |
| `value.length() < 4` | vrai pour les libellés très courts (souvent du bruit) |
| `cells["nb_docs"].value.toNumber() >= 2` | vrai pour les entités présentes dans au moins deux livres |
| `cells["description_wikidata"].value.contains("peintre")` | vrai quand la fiche proposée décrit un peintre |
| `cell.recon.match != null` | vrai quand une fiche Wikidata est choisie (colonne réconciliée) |
| `row.starred` / `row.flagged` | vrai pour les lignes à étoile / à drapeau |

## Expressions GREL pour transformer une colonne (menu « Éditer les cellules → Transformer… »)

| Expression | Effet |
|---|---|
| `value.trim()` | enlève les espaces de début et de fin |
| `value.toTitlecase()` | *rené descartes* devient *René Descartes* |
| `value.replace("ſ", "s")` | remplace le s long par un s |
| `value.replace(/\s+/, " ")` | réduit les espaces multiples à un seul |
| `value.replace(/[,.;:]+$/, "")` | enlève la ponctuation finale |
| `value.replace("v", "u")` | *CLAVDIVS* devient *CLAUDIUS* (à vérifier à l'aperçu, ce n'est pas toujours juste) |
| `cells["label"].value` | recopie la colonne *label* dans la colonne courante |
| `if(isBlank(value), cells["label"].value, value)` | ne recopie *label* que si la cellule est vide (c'est la recette de début de lot) |

## Les deux recettes, pour mémoire

- **Début de lot** : remplit `label_corrige` avec `label` quand elle est vide, puis **Réconcilier → Utiliser des valeurs comme identifiants…** sur `qid_propose` avec le service *Wikidata reconciliation (fr)*.
- **Fin de lot** : ajoute `qid_final` = `if(isNull(cell.recon), "", if(isNull(cell.recon.match), "AUCUN", cell.recon.match.id))` à partir de `qid_propose`, `supprimer` = `row.starred`, `doute` = `row.flagged`, puis vide `label_corrige` quand elle est identique à `label`.

Si une recette échoue (message d'erreur rouge), vous pouvez faire chaque étape à la main avec les expressions ci-dessus : le résultat est le même.
