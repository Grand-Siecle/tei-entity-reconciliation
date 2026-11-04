# Workflow : entites NER → Wikidata → registres TEI Publisher

## Vue d'ensemble

```
TEI files (ALTO→TEI pipeline)
    ↓ NER (CamemBERT + GLiNER)
    ↓
particDesc / settingDesc / standOff
    ↓ extraction
    ↓
CSV global (deduplique)
    ↓ reconciliation Wikidata (OpenRefine)
    ↓
CSV enrichi (QIDs, dates, coords, etc.)
    ↓ import
    ↓
Registres TEI Publisher (pb-persons, pb-places, etc.)
    ↓ reindexation
    ↓
Index / facettes / pages registres fonctionnels
```

---

## Etape 1 : Extraction CSV depuis la pipeline ALTO→TEI

A faire dans la pipeline de transformation (pas dans TEI Publisher).

### Format CSV attendu

```csv
id,type,label,label_norm,source_docs,total_mentions,confidence,wikidata_qid,wikidata_label
pers-12ab185f-...,person,Platon,platon,LIV0326_v2,3,high,,
place-cdecf9db-...,place,Athenae,athenae,LIV0326_v2,2,high,,
org-259938ed-...,org,le Philosophus,le philosophus,LIV0326_v2,3,mid,,
work-f4c310d2-...,work,euangelium,euangelium,LIV0326_v2,0,,,
```

**Colonnes :**

| Colonne | Description |
|---------|-------------|
| `id` | UUID de l'entite (du NER pipeline) |
| `type` | person / place / org / work / event |
| `label` | Forme telle que dans le TEI |
| `label_norm` | Forme normalisee (lowercase, sans diacritiques) pour deduplication |
| `source_docs` | Liste des documents ou l'entite apparait (separes par `\|`) |
| `total_mentions` | Nombre total de mentions inline (@ref) tous docs confondus |
| `confidence` | Niveau de confiance majoritaire (high/mid/low) |
| `wikidata_qid` | A remplir apres reconciliation |
| `wikidata_label` | Label officiel Wikidata (a remplir) |

### Deduplication

Deux entites de documents differents sont considerees identiques si :
- meme `type` ET meme `label_norm`
- OU si elles pointent vers le meme `wikidata_qid` (apres enrichissement)

Quand deux entites fusionnent :
- garder tous les `id` (pour mettre a jour les `@ref` dans les docs)
- cumuler `source_docs` et `total_mentions`
- prendre la `confidence` la plus haute

---

## Etape 2 : Reconciliation Wikidata

### Option A : OpenRefine (recommande)

1. Importer le CSV dans OpenRefine
2. Reconcilier la colonne `label` contre Wikidata :
   - `type=person` → reconcilier comme Q5 (human)
   - `type=place` → reconcilier comme Q515 (city) ou Q2221906 (geographic location)
   - `type=org` → reconcilier comme Q43229 (organization)
   - `type=work` → reconcilier comme Q47461344 (written work)
3. Valider/corriger les matchs manuellement
4. Enrichir avec les proprietes Wikidata :
   - Personnes : P569 (naissance), P570 (mort), P106 (occupation), P213 (ISNI)
   - Lieux : P625 (coordonnees), P17 (pays), P1566 (Geonames ID)
   - Oeuvres : P50 (auteur), P577 (date publication)
5. Exporter en CSV enrichi

### Option B : SPARQL batch

Pour les entites a haute confiance, requete SPARQL directe :

```sparql
SELECT ?item ?itemLabel ?birth ?death WHERE {
  VALUES ?name { "Platon"@fr "Aristoteles"@la "Tertullien"@fr }
  ?item rdfs:label ?name .
  ?item wdt:P31 wd:Q5 .
  OPTIONAL { ?item wdt:P569 ?birth }
  OPTIONAL { ?item wdt:P570 ?death }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,la,en" }
}
```

### Reconciliation avec les referentiels ArtErm (a faire)

Wikidata seul ne suffit pas pour les entites du **domaine artistique** : il faut
**aussi** reconcilier contre les referentiels **ArtErm** (terminologie de l'histoire
de l'art). Cela concerne en priorite les registres `artworks` (objets / oeuvres
d'art), `materials` et `techniques`, ou ArtErm fournit un vocabulaire controle plus
precis et adapte au corpus que les QIDs generiques de Wikidata.

A prevoir :
- ajouter un identifiant `idno[@type='arterm']` (en plus du `idno[@type='wikidata']`)
  dans les entrees concernees ;
- traiter ArtErm comme reference d'autorite **prioritaire** pour materiaux /
  techniques / objets, Wikidata restant le complement (dates, coords, notoriete) ;
- aligner les libelles principaux sur la terminologie ArtErm quand un match existe.

---

## Etape 3 : Import CSV enrichi → registres TEI Publisher

Script a fournir : `scripts/csv-to-registers.py` (ou XQuery).

### Ce que le script fait

1. Lit le CSV enrichi
2. Pour chaque entite avec un `wikidata_qid` :
   - Cree ou met a jour l'entree dans le fichier registre correspondant
   - Genere un `xml:id` stable (ex: `person-000001`, `place-000001`)
   - Ajoute les metadonnees Wikidata (dates, coords, identifiants)
3. Produit un fichier de mapping `id_mapping.csv` :
   ```csv
   old_id,new_register_id,type
   pers-12ab185f-...,person-000001,person
   place-cdecf9db-...,place-000001,place
   ```

### Format registre cible

**Personnes** (`data/registers/persons.xml`) :
```xml
<person xml:id="person-000001">
    <persName type="main">Platon</persName>
    <persName type="sort">Platon</persName>
    <birth><date when="-0428"/></birth>
    <death><date when="-0348"/></death>
    <occupation>Philosophe</occupation>
    <idno type="wikidata">Q859</idno>
    <idno type="isni">...</idno>
    <note>Philosophe grec, fondateur de l'Academie d'Athenes</note>
</person>
```

**Lieux** (`data/registers/places.xml`) :
```xml
<place xml:id="place-000001">
    <placeName type="main">Athenes</placeName>
    <placeName type="sort">Athenes</placeName>
    <location><geo>37.9838 23.7275</geo></location>
    <country>Grece</country>
    <idno type="wikidata">Q1524</idno>
    <idno type="geonames">264371</idno>
    <note>Capitale de la Grece, centre philosophique antique</note>
</place>
```

---

## Etape 4 : Mise a jour des @ref dans les documents TEI

Apres creation des registres, les `@ref` inline des documents doivent pointer vers
les ids de registre au lieu des UUIDs NER.

```
Avant : <persName ref="#pers-12ab185f-..." cert="high">Platon</persName>
Apres : <persName ref="#person-000432" cert="high">Platon</persName>
```

> **On garde le `#`.** L'ODD (`resources/odd/grand_siecle.odd`) fait
> `replace(@ref, '^#', '')` pour la cle de lookup **et** ne surligne les oeuvres
> que si `starts-with(@ref, '#work-')`. Donc `#person-000432` est la forme correcte
> pour tous les types. (Une ancienne version de ce doc montrait `person-000001`
> sans `#` : c'etait faux.)

### Outil : `scripts/rewrite_refs.py`

Reecrit les `@ref` du corps en streaming (memoire constante, OK pour des fichiers
>200 Mo) a partir de `data/registers/id_mapping.csv`, **et reconstruit les listes
internes du document** (`teiHeader`, `standOff`) avec les ids de registre.

```bash
python3 scripts/rewrite_refs.py --dry-run data/LIV0001_reconciled.tei.xml   # compte, n'ecrit rien
python3 scripts/rewrite_refs.py --out /tmp/preview data/LIV0001_*.xml        # ecrit ailleurs (originaux intacts)
python3 scripts/rewrite_refs.py data/*.tei.xml                               # reecriture en place (atomique)
python3 scripts/rewrite_refs.py --no-standoff data/*.tei.xml                 # ancien comportement : @ref seulement
```

> **Issue #1 (corrigee).** Les versions precedentes ne reecrivaient que `@ref` et
> laissaient les `xml:id` UUID dans le `standOff` / `particDesc` : le corps pointait
> vers `#person-000432` alors que la liste du document gardait `pers-<uuid>`. Les
> jointures entete <-> corps par id ne trouvaient plus rien et les listes devenaient
> orphelines ([issue #1](https://github.com/Grand-Siecle/tei-entity-reconciliation/issues/1)).
> Correctif retenu (option 1 de l'issue) : les listes internes sont reconstruites avec
> les ids de registre. `--no-standoff` restaure l'ancien comportement.

**Invariants** (sinon corruption) :

1. **Une seule entree par id de registre dans les listes du document.** Le mapping
   est *plusieurs-vers-un* (la deduplication a fusionne plusieurs UUID vers un meme
   id de registre) : renommer betement les `xml:id` creerait des id en double -> XML
   invalide / erreur d'indexation eXist. Dans les blocs `<teiHeader>` et `<standOff>`,
   les entrees qui tombent sur le meme id sont donc **fusionnees** : la premiere (ordre
   du document) est gardee et renommee, les noms des autres sont ajoutes en
   `type="variant"` quand le texte differe, le `@cert` le plus fort est conserve, les
   autres entrees sont supprimees. Les jetons de `@target` / `@corresp` qui sont des
   UUID du mapping sont reecrits aussi (`linkGrp/link` reste valide). Les ids de
   mention (`pers-<uuid>-m1`) et de segments (`ent-xxxx-0`) ne bougent jamais.
2. **Dans le `<text>`, seul l'attribut `@ref` est modifie** (regex ancree sur
   `\sref="..."`, traitement ligne par ligne).
3. **Les refs absentes du mapping restent telles quelles** (entites non reconciliees,
   ou supprimees en curation). Elles continuent de pointer vers l'entree locale du
   document, laissee intacte. C'est normal de voir quelques `#event-<uuid>` subsister.
4. **Prudence et idempotence.** Un bloc `<teiHeader>` / `<standOff>` qui ne se parse
   pas -> avertissement `WARN ... @ref seulement` et repli sur la seule reecriture des
   `@ref` pour ce fichier. Un fichier sans rien a changer reste identique a l'octet
   pres ; un second passage ne change rien (`COUNT 0`).

`COUNT <n>` (et `TOTAL`) compte desormais les `@ref` reecrits **plus** les
modifications des listes internes (entrees renommees, fusionnees, `@target` reecrits).

### Upload : `scripts/upload_tei.sh`

La reecriture change le fichier -> il faut le re-pousser sur eXist. Si la **sync
VS Code** est active, sauver/toucher le fichier suffit. Si elle est **indisponible**
(l'extension peut planter sur de gros lots), utiliser l'upload REST direct, un par un
avec pause (chaque PUT est synchrone -> la cadence se regule, la pause soulage le serveur) :

```bash
# registres D'ABORD (les @ref doivent pouvoir resoudre), puis documents :
scripts/upload_tei.sh data/registers/*.xml
PAUSE=4 scripts/upload_tei.sh data/*.tei.xml
```

### Reproduire a l'echelle (corpus complet)

```bash
# 0. (pre-requis) pipeline NER deja joue -> data/registers/*.xml + id_mapping.csv a jour
# 1. controle a blanc : combien de refs vont changer, sur tout le corpus
python3 scripts/rewrite_refs.py --dry-run data/*.tei.xml | tail -1      # TOTAL <n>
# 2. reecriture en place de tous les documents
python3 scripts/rewrite_refs.py data/*.tei.xml
# 3. upload registres puis documents (REST direct, ordre important)
scripts/upload_tei.sh data/registers/*.xml
PAUSE=4 scripts/upload_tei.sh data/*.tei.xml
# 4. reindexation (Etape 5)
```

**Verifier apres coup** (cote serveur), aucun UUID ne doit subsister, sauf entites
non reconciliees :

```bash
curl -s -u admin: ".../data/LIV0001_reconciled.tei.xml" \
  | grep -coE 'ref="#(pers|place|org|work)-[0-9a-f]{8}-'      # attendu : 0
```

> **Idealement** cette etape se fait **dans la pipeline ALTO->TEI**, pour uploader
> directement des fichiers corrects (un seul upload) plutot que pousser puis corriger.
> Le post-processing ci-dessus est le filet pour les documents deja en base.

> **Pilote du 2026-06-13** : ~45 980 refs reecrites sur 35 documents (jusqu'a 219 Mo /
> 8362 refs pour un seul fichier), 0 echec. Les types `person/place/org/work` resolvent
> (registres + `$config:register-map` deja cables) ; `event/artwork/material/technique/
> date` sont reecrits mais ne resoldront en pages registre qu'une fois cables dans
> `config.xqm` et `collection.xconf` (voir checklist).

---

## Etape 5 : Reindexation

Apres import des registres et mise a jour des refs :

```
POST /api/odd?odd=grand_siecle.odd     (recompiler l'ODD)
xmldb:reindex('/db/apps/GdSiecle/data') (reindexer la collection)
```

Les pages `/people`, `/places`, `/bibliography` seront alors fonctionnelles.

---

## Fichiers concernes dans l'application

| Fichier | Role |
|---------|------|
| `data/registers/persons.xml` | Registre personnes (xml:id="pb-persons") |
| `data/registers/places.xml` | Registre lieux (xml:id="pb-places") |
| `data/registers/organizations.xml` | Registre organisations (xml:id="pb-organizations") |
| `data/registers/works.xml` | Registre oeuvres (xml:id="pb-works") |
| `collection.xconf` | Index Lucene (deja configure pour les registres) |
| `modules/config.xqm` | Mapping registre (deja configure) |
| `modules/registers-api.xql` | API registres (existant) |

---

## Implementation (2026-06-13)

Le workflow est implemente en 3 scripts Python (stdlib uniquement), executables
depuis la racine du projet :

```bash
python3 scripts/extract_entities.py      # TEI -> build/ner/entities_clusters.json (+ .csv)
python3 scripts/reconcile_wikidata.py    # -> build/ner/entities_reconciled.json (resumable, cache)
python3 scripts/build_registers.py       # -> data/registers/*.xml + id_mapping.csv + rapport
python3 scripts/rewrite_refs.py data/*.tei.xml   # Etape 4 : @ref + listes internes UUID -> id registre
scripts/upload_tei.sh data/registers/*.xml data/*.tei.xml   # push REST vers eXist (si sync VS Code HS)
```

Travail en equipe (voir plus bas) : `scripts/export_lots.py` (lots de relecture) et
`scripts/import_curation.py` (decisions -> `data/registers/curation.csv`).

Options utiles : `reconcile_wikidata.py --min-mentions N --types person,place --limit N`.
Le cache (`build/ner/wd_cache.json`) rend les relances quasi instantanees ;
`scripts/slim_cache.py` reduit sa taille si besoin.

### Types d'entites couverts

Au-dela des 4 types prevus initialement, le NER produit aussi des entites
`event`, `artwork` (objets/oeuvres d'art), `material`, `technique` et `date`.
Le pipeline genere donc **9 registres** :

| Registre | Fichier | Element racine |
|----------|---------|----------------|
| Personnes | `persons.xml` | `listPerson` |
| Lieux | `places.xml` | `listPlace` |
| Organisations | `organizations.xml` | `listOrg` |
| Oeuvres | `works.xml` | `listBibl` |
| Evenements | `events.xml` | `listEvent` |
| Objets / oeuvres d'art | `artworks.xml` | `listObject` |
| Materiaux | `materials.xml` | `taxonomy` / `category` |
| Techniques | `techniques.xml` | `taxonomy` / `category` |
| Chronologie | `dates.xml` | `list[@type='chronology']` |

### Qualite de la reconciliation

- Validation **par type via les proprietes** Wikidata (lieu = coordonnees/GeoNames,
  personne = dates/occupation, oeuvre = auteur...) pour eviter les faux positifs.
- Filtre de **periode** (personne nee apres ~1660 rejetee, corpus XVIIe).
- Score par **notoriete** (nombre de sitelinks) + plausibilite geographique.
- **Niveau de confiance** (high/medium/low). Le QID n'est inscrit que pour
  high/medium ; les low restent en `note[@type='wikidata-candidates']` (curation).
- Conforme a la politique WMF : User-Agent descriptif, `maxlag`, requetes en serie.

---

## Comment ca marche (pour relecture / modification)

Les 3 etapes, ce qu'elles font, et **ou regler** si on veut changer un comportement.

### 1. Clustering — `scripts/extract_entities.py`

But : transformer des milliers de formes de surface bruitees (OCR) en une liste
d'entites-autorite dedupliquees.

- Lit deux sources dans chaque TEI : les definitions du `standOff`
  (`person`/`place`/`org`/`event`/`bibl`/`object`) **et** toutes les mentions inline
  qui portent un `@ref` (c'est la seule source pour `material`/`technique`/`date`,
  absents du standOff).
- Chaque entite garde : formes de surface (comptees), docs sources, nb de mentions.
- **Cle de deduplication** = `(type, label_norm)`. `label_norm` = minuscule, sans
  diacritiques, sans ponctuation, sans article de tete (`norm_label`). Deux entites
  de docs differents avec le meme `label_norm` fusionnent ; on cumule mentions +
  docs + UUID NER.
- **Libelle d'affichage** choisi par `best_label` (score : frequence, majuscule
  initiale, multi-mots, longueur). Les autres formes deviennent des `variants`.

| Pour changer... | Modifier |
|---|---|
| Regles de normalisation (articles, graphies) | `norm_label`, `LEADING` |
| Choix du libelle principal | `best_label` (poids du score) |
| Elements inline pris en compte | la liste dans la boucle « mentions inline » |

> Limite connue : la fusion est **exacte** sur `label_norm`. Les variantes OCR
> trop eloignees (`jesvs` vs `jesu Christe`) ne fusionnent pas ici — elles le font
> seulement si elles tombent sur le **meme QID** apres reconciliation.

### 2. Reconciliation — `scripts/reconcile_wikidata.py`

But : associer chaque cluster a un QID Wikidata **fiable**, sans faux positifs.

- **Recherche par etapes + sortie anticipee** (`reconcile_one`) : on interroge
  d'abord le label en `fr`, puis formes nettoyees / `la` / variantes ; **on s'arrete
  des qu'un match confiant est trouve**. Une entite notable coute ~2 appels, pas 24.
  Le budget d'etapes depend de la frequence (un cluster vu 2 fois cherche moins).
- **Validation par type** (`ACCEPT`) : on n'accepte un candidat que s'il a les
  proprietes attendues — lieu = coords/GeoNames (`P625`/`P1566`), personne =
  dates/occupation, oeuvre = auteur (`P50`)... C'est ce qui empeche un lieu de
  matcher une personne.
- **Filtre d'anachronisme** : personne nee apres ~1660, evenement date apres ~1700
  → rejete (corpus XVIIe). C'est ce qui fait que « Concile » → Trente, pas Vatican II.
- **Score** (`score_candidate`) : `(similarite_nom, notoriete, rang)`.
  - similarite : 3 = label/alias exact, 2 = sous-chaine, 1 = dernier mot commun, 0 = rejet.
  - notoriete = nombre de sitelinks (un homonyme obscur perd contre le celebre).
  - lieux : bonus si pays plausible (Europe/Mediterranee), malus sinon
    (`place_geo_bonus` + `PLAUSIBLE_PLACE_Q`) → Port-Royal = l'abbaye, pas la Jamaique.
- **Confiance** : `high` (exact + notoriete/autorite), `medium`, `low`. Materiaux /
  techniques / objets : `low` force sauf si identifiant Getty **AAT** present
  (sinon « terre » deviendrait la planete).

| Pour changer... | Modifier |
|---|---|
| Ce qu'on accepte par type | les fonctions `accept_*` + dict `ACCEPT` |
| Bornes d'anachronisme | `> 1660` (person), `> 1700` (event) dans `score_candidate` |
| Importance notoriete vs nom | le tuple de score dans `score_candidate` |
| Pays plausibles (lieux) | `PLAUSIBLE_PLACE_Q` |
| Seuils high/medium/low | bloc « confiance » a la fin de `reconcile_one` |
| Proprietes recoltees par type | `build_record` |
| Politesse API (workers, maxlag, UA) | `max_workers`, `MAXLAG`, `UA` |

> Resumable : tout passe par `wd_cache.json` (recherches + entites *slimmees*).
> Relancer ne refait que ce qui manque. `--types` / `--min-mentions` limitent la portee.

### 3. Generation des registres — `scripts/build_registers.py`

But : ecrire les 9 fichiers TEI + le mapping.

- **Regle d'assertion** : le QID n'est inscrit (`idno[@type='wikidata']` + dates,
  coords, identifiants...) **que si la confiance est high ou medium** (`ASSERT_CONF`).
  Les `low` ne mettent pas de QID mais laissent une piste
  `note[@type='wikidata-candidates']`.
- **Filtrage des variantes** (`related_variant`) : on ne garde comme `variant` que
  les formes vraiment apparentees au libelle (sous-chaine, prefixe commun, faible
  distance de Levenshtein) → enleve le bruit (`eloquence` n'est plus une variante
  de « Rome »).
- **Tracabilite** sur chaque entree : `note[@type='ner-ids']` (UUID fusionnes),
  `sources`, `mentions`, `reconciliation-confidence`. Plus `id_mapping.csv` global
  (UUID NER → id registre) pour reecrire les `@ref` plus tard.
- **Ids stables** (`scripts/idregistry.py`, `data/registers/id_registry.csv`) : un id
  est attribue une fois pour toutes (cle `(type, label_norm)`, sinon chevauchement
  majoritaire des UUID NER, sinon nouvel id) et n'est jamais reutilise. Ajouter des
  documents ne renumerote donc plus les entites existantes.
- **Surcouche de curation** (`scripts/curation.py`, `data/registers/curation.csv`) :
  reappliquee a chaque build (voir « Travail en équipe »). Les clusters apres curation
  sont ecrits dans `build/ner/entities_registered.json` (entree de `export_lots.py`).

| Pour changer... | Modifier |
|---|---|
| Quels niveaux inscrivent le QID | `ASSERT_CONF` |
| Severite du filtre de variantes | `related_variant` (seuils) |
| Structure XML d'un type | la fonction `gen_*` correspondante |
| Mapping fichier / racine / prefixe d'id | dict `TYPES` |

---

## Travail en équipe (curation incrementale)

Principe : les fichiers generes sont jetables et entierement regeneres a chaque build.
**Les decisions humaines ne vivent que dans `data/registers/curation.csv`**, indexees
par l'id de registre stable conserve dans `data/registers/id_registry.csv`. Ces deux
fichiers sont **commites dans git** (le `.gitignore` exclut les autres CSV de
`data/`) et ne doivent jamais etre supprimes ; tout le reste se reconstruit.

### Le cycle

```
extraction → reconciliation → build → export des lots → relecteurs (OpenRefine / tableur)
      → import des decisions → build → reecriture depuis les documents ORIGINAUX
      ↺ a chaque ajout de documents ou nouveau lot relu
```

```bash
# 1-3. extraction, reconciliation, build comme d'habitude
#      (ids pris dans id_registry.csv, curation.csv reappliquee)
python3 scripts/build_registers.py
# 4. lots de relecture (CSV), un fichier par type et par tranche de 500 lignes
python3 scripts/export_lots.py --min-mentions 2 --out build/ner/lots
# 5. les relecteurs editent les lots (kit ci-dessous), puis :
python3 scripts/import_curation.py --qui "Alice" --known-ids data/registers/id_registry.csv \
  --dry-run lots_alice/*.csv        # bilan seulement
python3 scripts/import_curation.py --qui "Alice" --known-ids data/registers/id_registry.csv \
  lots_alice/*.csv                  # ecrit data/registers/curation.csv
# 6. rebuild : decisions appliquees, ids inchanges
python3 scripts/build_registers.py
# 7. reecrire une copie fraiche des documents ORIGINAUX avec le nouveau mapping
python3 scripts/rewrite_refs.py --out data/rewritten originaux/*.tei.xml
# 8. controle (les entites supprimees gardent leurs refs locales : --ignore-ref si besoin)
tests/validate_registers.py data/registers --docs "data/rewritten/*.tei.xml" \
  --curation data/registers/curation.csv
git add data/registers/id_registry.csv data/registers/curation.csv
```

On reecrit **depuis les originaux** : un document deja reecrit ne porte plus les UUID
NER, il ne profiterait donc pas d'une fusion ou d'une suppression decidee ensuite.

A l'ajout de documents : les entites existantes gardent leur id, les decisions
existantes s'appliquent toujours, et l'export suivant marque chaque ligne `nouveau`,
`modifié` (libelle ou QID propose change) ou `inchangé` (colonne `statut`, calculee
contre `build/ner/lots_state.json` ; `--only-new` n'exporte que les deux premiers).

### Kit du relecteur

Tout ce dont un relecteur a besoin est dans [`relecteurs/`](../relecteurs/) :

- [`relecteurs/README.md`](../relecteurs/README.md) — la marche a suivre en bref ;
- [`relecteurs/guide_relecteur_openrefine.pdf`](../relecteurs/guide_relecteur_openrefine.pdf)
  — le guide OpenRefine pas a pas, avec captures (source :
  [`relecteurs/guide_relecteur_openrefine.md`](../relecteurs/guide_relecteur_openrefine.md)) ;
- les recettes OpenRefine a coller dans *Undo / Redo → Apply* :
  [`relecteurs/recettes/debut_de_lot.json`](../relecteurs/recettes/debut_de_lot.json)
  (debut de lot : copie `label` dans `label_corrige`, transforme `qid_propose` en
  correspondances Wikidata) et
  [`relecteurs/recettes/fin_de_lot.json`](../relecteurs/recettes/fin_de_lot.json)
  (fin de lot, avant l'export CSV : ajoute `qid_final`, `qid_juge`, `supprimer`,
  `doute`).

Les relecteurs rendent chaque lot renomme `<lot>_relu_<prenom>.csv`, avec `_complet`
a la fin (`person_lot01_relu_marie_complet.csv`) quand ils ont relu **tout** le lot :
c'est seulement dans ce cas qu'une proposition laissee telle quelle vaut `ok` (voir
les regles d'import ci-dessous).

Un simple tableur convient aussi : remplir les colonnes de decision du lot et
enregistrer en CSV (separateur `,` `;` ou tabulation, UTF-8 avec ou sans BOM ;
`export_lots.py --excel` ecrit `;` + BOM pour Excel FR).

### Vocabulaire des decisions (`curation.csv`)

Colonnes : `id,type,decision,qid_corrige,fusion_vers,label_corrige,type_corrige,commentaire,qui,date,source`.

| `decision` | Effet au build |
|---|---|
| *(vide)* | aucune decision ; `label_corrige` / `type_corrige` / `commentaire` s'appliquent quand meme |
| `ok` | asserte le QID propose quelle que soit sa confiance (confiance `curated`). `qid_corrige` garde le QID accepte a la relecture (ecrit par l'import) : si la proposition actuelle differe, rien n'est asserte et le build signale `proposition changee depuis la relecture (<ancien> -> <nouveau>)` ; `qid_corrige` vide → la proposition actuelle est assertee |
| `corriger` | `qid_corrige` rempli → ce QID (proprietes recuperees sur Wikidata), confiance `curated` ; `qid_corrige` vide → aucun QID (`rejected`) |
| `fusionner` | fusion dans `fusion_vers` (chaines A→B→C resolues, cycles signales et ignores) |
| `supprimer` | entite retiree des registres ; ses UUID ne sont plus mappes (les refs restent locales) |
| `doute` | aucun changement (seulement la note de curation) |

Toute decision non vide ecrit `<note type="curation" resp="qui">decision</note>` sur
l'entree (`resp` si `qui` est rempli) ; un `corriger` sans QID ecrit en plus
`<note type="curation">rejected</note>`. `label_corrige` (nouveau libelle principal)
et `type_corrige` (changement de registre) s'appliquent avec n'importe quelle
decision. Les erreurs (cible inconnue,
cycle...) ne bloquent jamais le build : elles sont signalees.

### Import : comment une ligne devient une decision

`import_curation.py` lit les lots edites (tableur) et les exports OpenRefine, et
applique cet ordre. Le **QID implique** d'une ligne est `qid_corrige` si sa decision
est `corriger`, sinon `qid_propose`.

1. **etoile** (`starred` / `supprimer` vrai) ou decision `supprimer` → `supprimer` ;
2. decision `fusionner`, ou `fusion_vers` rempli sans decision → `fusionner` ;
3. **drapeau** (`flagged` / `doute` vrai) → `doute` : il l'emporte sur tout `ok`
   deduit (un `qid_final` different du QID implique est garde dans `qid_corrige`,
   pour information) ;
4. **`qid_final`** (colonne de la recette de fin, QID ou URL Wikidata) : `""` =
   aucune information ; `AUCUN` = match rejete → `corriger` sans QID (si un QID etait
   implique) ; QID different du QID implique → `corriger` avec ce QID ; egal a
   `qid_propose` sans decision → `ok` **seulement si le jugement est explicite** :
   `qid_juge` = `single` / `similar` (le
   relecteur a coche le candidat a la main ; la recette de debut apparie toutes les
   propositions en masse, `qid_juge` = `mass`), **ou** lot declare complet
   (`--lot-complet`, ou `complet` dans le nom du fichier) et `confiance` `high` ou
   `medium` ; sinon aucune decision. Un fichier exporte avec une ancienne recette de
   fin (colonne `qid_final` sans `qid_juge`) demande donc `_complet` ou des decisions
   explicites ; un simple tableur n'a pas de `qid_final` et passe par l'etape 5 ;
5. decision explicite `ok` / `corriger` / `doute` (`qid_corrige` seul → `corriger`) ;
6. seulement `label_corrige` / `type_corrige` / `commentaire` → ligne gardee,
   decision vide.

Une ligne `ok` inscrit le QID accepte dans `qid_corrige` (`qid_final`, sinon
`qid_propose`). (vrai = `true|1|oui|x|yes|vrai`.) Les lignes d'un meme type
partageant un `label_corrige` non vide (sur l'ensemble des fichiers importes) sont
**fusionnees** dans celle qui a le plus de mentions (`fusionner` → son id), sauf
decision explicite ; une ligne au `label_corrige` vide rejoint ce groupe quand son
propre `label` est egal a ce `label_corrige`. Un id present dans plusieurs fichiers :
la derniere ligne portant une decision non vide l'emporte (une ligne sans decision
ne la remplace jamais) et un avertissement liste ces ids. Une nouvelle ligne remplace **toute** la
ligne de `curation.csv` de meme id : un fichier de relecteur sans les colonnes
`label_corrige` / `type_corrige` / `commentaire` efface donc leurs valeurs
anterieures. Re-importer tel quel un lot pre-rempli garde `qui` / `date` / `source`
d'origine. `--known-ids` ignore les ids absents de `id_registry.csv`.

### Garde-fou de l'export

Les relecteurs editent les lots sur place. `export_lots.py` ne remplace donc que les
lots listes dans le `lots_manifest.csv` precedent et **refuse** (sauf `--force`) si
l'un d'eux a ete modifie sans avoir ete importe, ou si un fichier cible existe hors
manifeste. Le controle compare les contenus, pas les dates : l'export note
l'empreinte sha256 de chaque lot dans `<out>/.lots_written.json`, et
`import_curation.py` note celle de chaque fichier importe dans le `.lots_written.json`
du repertoire de ce fichier ; un lot n'est refuse que si son empreinte ne correspond a
aucune des deux. `.lots_written.json` peut etre supprime sans risque : pour un lot
sans empreinte, le controle se rabat sur la mtime du manifeste (moins fiable).

## Checklist

- [x] Extraction globale des entites depuis le standOff + mentions inline `@ref`
- [x] Deduplication par label normalise + type (variantes OCR fusionnees)
- [x] Reconciliation Wikidata automatisee (API wbsearch + wbget, validee par type)
- [x] Enrichissement des proprietes (dates, coords, identifiants externes)
- [x] Generation des registres TEI (9 types) + `id_mapping.csv`
- [x] Rapport de reconciliation (`build/ner/reconciliation_report.md`)
- [ ] Reconciliation avec les referentiels ArtErm (artworks / materials / techniques)
- [x] Ids de registre stables (`data/registers/id_registry.csv`, jamais reutilises)
- [x] Outillage de curation en equipe : `curation.csv` reappliquee a chaque build,
      lots de relecture (`export_lots.py`), import des decisions (`import_curation.py`),
      kit relecteur (`relecteurs/`)
- [ ] Curation manuelle des matchs low / ambigus (en cours, par lots)
- [x] Reecriture des `@ref` dans les documents TEI via `id_mapping.csv`
      (`scripts/rewrite_refs.py` ; format `#person-NNNNNN` ;
      pilote 2026-06-13 : ~45 980 refs sur 35 docs, 0 echec)
- [x] Listes internes (`teiHeader` / `standOff`) reconstruites avec les ids de
      registre, une entree par id (issue #1 ; `--no-standoff` = ancien comportement)
- [ ] Wiring app : ajouter event/material/technique/artwork/date a
      `config.xqm` (`$config:register-map`) et `collection.xconf`
- [ ] Upload registres dans eXist-db + reindexation
- [ ] Verification des pages `/people`, `/places`, `/bibliography`
