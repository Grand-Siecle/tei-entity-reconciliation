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

Reecrit les `@ref` en streaming (memoire constante, OK pour des fichiers >200 Mo)
a partir de `data/registers/id_mapping.csv`.

```bash
python3 scripts/rewrite_refs.py --dry-run data/LIV0001_reconciled.tei.xml   # compte, n'ecrit rien
python3 scripts/rewrite_refs.py --out /tmp/preview data/LIV0001_*.xml        # ecrit ailleurs (originaux intacts)
python3 scripts/rewrite_refs.py data/*.tei.xml                               # reecriture en place (atomique)
```

**Deux invariants critiques** (sinon corruption) :

1. **Ne reecrire QUE l'attribut `@ref`, jamais les `xml:id` du standOff.** Le mapping
   est *plusieurs-vers-un* (la deduplication a fusionne plusieurs UUID vers un meme
   id de registre). Reecrire les `xml:id` creerait des id en double dans un document
   -> XML invalide / erreur d'indexation eXist. Le script ancre sa regex sur
   `\sref="..."` exactement pour ca.
2. **Les refs absentes du mapping restent telles quelles** (entites non reconciliees).
   Elles continuent de pointer vers le standOff local du document, qui reste valide.
   C'est normal de voir quelques `#event-<uuid>` subsister.

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
python3 scripts/rewrite_refs.py data/*.tei.xml   # Etape 4 : @ref UUID -> id registre
scripts/upload_tei.sh data/registers/*.xml data/*.tei.xml   # push REST vers eXist (si sync VS Code HS)
```

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

| Pour changer... | Modifier |
|---|---|
| Quels niveaux inscrivent le QID | `ASSERT_CONF` |
| Severite du filtre de variantes | `related_variant` (seuils) |
| Structure XML d'un type | la fonction `gen_*` correspondante |
| Mapping fichier / racine / prefixe d'id | dict `TYPES` |

## Checklist

- [x] Extraction globale des entites depuis le standOff + mentions inline `@ref`
- [x] Deduplication par label normalise + type (variantes OCR fusionnees)
- [x] Reconciliation Wikidata automatisee (API wbsearch + wbget, validee par type)
- [x] Enrichissement des proprietes (dates, coords, identifiants externes)
- [x] Generation des registres TEI (9 types) + `id_mapping.csv`
- [x] Rapport de reconciliation (`build/ner/reconciliation_report.md`)
- [ ] Reconciliation avec les referentiels ArtErm (artworks / materials / techniques)
- [ ] Curation manuelle des matchs low / ambigus
- [x] Reecriture des `@ref` dans les documents TEI via `id_mapping.csv`
      (`scripts/rewrite_refs.py` ; format `#person-NNNNNN`, seul `@ref` modifie ;
      pilote 2026-06-13 : ~45 980 refs sur 35 docs, 0 echec)
- [ ] Wiring app : ajouter event/material/technique/artwork/date a
      `config.xqm` (`$config:register-map`) et `collection.xconf`
- [ ] Upload registres dans eXist-db + reindexation
- [ ] Verification des pages `/people`, `/places`, `/bibliography`
