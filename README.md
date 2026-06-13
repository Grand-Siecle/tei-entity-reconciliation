# tei-entity-reconciliation

> Turn raw NER output in a TEI corpus into clean **authority registers**, reconciled
> against external referentials (Wikidata), and rewrite the documents' `@ref` so every
> inline mention points at a stable register id.

A small, dependency-free pipeline (Python 3 standard library only) that takes TEI
documents already annotated by NER and produces TEI Publisher–ready authority files
for **9 entity types**: persons, places, organizations, works, events, artworks,
materials, techniques, and a chronology.

It was extracted from the [Grand Siècle](https://github.com/GrandSiecle) TEI Publisher
project to be reusable on any TEI corpus.

---

## What it does

```
TEI documents (NER already run: standOff defs + inline @ref)
      │
      ▼  1. extract_entities.py
cluster the surface forms into deduplicated authority entities
      │   (key = type + normalized label; OCR variants merged)
      ▼  2. reconcile_wikidata.py
match each cluster to a Wikidata QID — validated PER TYPE by properties
      │   (place→coords/GeoNames, person→dates/occupation, work→author…),
      │   period filter, notability score, confidence high/medium/low
      ▼  3. build_registers.py
write 9 TEI registers + id_mapping.csv (NER UUID → register id)
      │
      ▼  4. rewrite_refs.py
rewrite the documents' @ref: #pers-<uuid> → #person-000432
```

Uploading the result into a database (e.g. eXist-db / TEI Publisher) and reindexing
are **deployment concerns of the consuming application**, intentionally out of scope here.

---

## Why

NER tools emit per-document, per-mention identifiers (UUIDs). To get working
person/place/work indexes you need to (a) **deduplicate** mentions into canonical
entities across the whole corpus, (b) **reconcile** them to an external authority so
they carry stable identifiers and metadata, and (c) **re-point** the documents at those
canonical entities. This repo does exactly those three things, conservatively (no false
positives, no data corruption — see the invariants below).

---

## Requirements

- **Python 3.8+** — standard library only (`xml.etree`, `urllib`, `csv`…). No `pip install`.
- `curl` only for the optional eXist upload helper (kept in the consuming app, not here).
- Reconciliation step needs network access to the Wikidata API.

---

## Repository layout

```
scripts/
  extract_entities.py     # 1. NER → deduplicated entity clusters
  reconcile_wikidata.py   # 2. clusters → Wikidata QIDs (resumable, cached)
  build_registers.py      # 3. → 9 TEI registers + id_mapping.csv
  rewrite_refs.py          # 4. rewrite @ref in TEI docs (UUID → register id)
  slim_cache.py           # shrink the Wikidata cache file
  finalize.sh             # optional orchestration helper (original-run convenience)
docs/
  workflow.fr.md          # detailed design notes (French, original project doc)
examples/sample/data/
  sample_reconciled.tei.xml   # tiny synthetic fixture (all 9 types)
tests/
  run_tests.sh            # offline suite (no network, no deps)
  test_rewrite_refs.py    # @ref rewriting invariants
  test_pipeline.sh        # end-to-end extract→build→rewrite→validate
  validate_registers.py   # reusable health-check / QA tool
  test_wikidata_smoke.sh  # optional live API check
data/                     # put your corpus here (gitignored); registers written to data/registers/
build/                    # intermediate artifacts + Wikidata cache (gitignored)
```

By default the scripts assume the repo layout: inputs in `data/`, artifacts in
`build/ner/`, registers in `data/registers/`. **All of these are overridable** — see
[Configuration](#configuration).

---

## Quickstart (on the bundled sample)

```bash
# 1. extract entity clusters from the sample document
TEI_GLOB="examples/sample/data/sample_reconciled.tei.xml" NER_OUT=build/ner \
  python3 scripts/extract_entities.py

# 2. reconcile to Wikidata (network). Always set a contact (WMF politeness policy).
WIKIDATA_CONTACT="you@example.org" NER_OUT=build/ner \
  python3 scripts/reconcile_wikidata.py --types person,place

# 3. build the 9 registers + id_mapping.csv
NER_OUT=build/ner REGISTERS_DIR=data/registers CORPUS_NAME="My Corpus" \
  python3 scripts/build_registers.py

# 4. rewrite the document @ref using the mapping
python3 scripts/rewrite_refs.py --mapping data/registers/id_mapping.csv \
  examples/sample/data/sample_reconciled.tei.xml
```

On a real corpus, drop your `*_reconciled.tei.xml` files in `data/` and run the four
steps with the defaults (no env vars needed).

---

## The four steps

### 1. `extract_entities.py` — clustering
Reads each TEI file's `standOff` definitions **and** every inline `@ref` mention
(materials / techniques / dates only ever appear inline). Deduplicates by
`(type, normalized_label)`, merging OCR variants and accumulating source docs, mention
counts and the merged NER UUIDs. → `build/ner/entities_clusters.json` (+ `.csv`).

### 2. `reconcile_wikidata.py` — reconciliation
Queries the Wikidata API and **validates each candidate by the properties expected for
its type** to avoid false positives (a place must have coordinates/GeoNames, a person
birth/death or occupation, a work an author…). Adds a period filter (17th-c. corpus),
a notability score (sitelinks) and a `high/medium/low` confidence. **Resumable**: a disk
cache (`build/ner/wd_cache.json`) means re-runs only do what's missing. WMF-compliant
(descriptive User-Agent, `maxlag`, serial-ish requests).

```bash
python3 scripts/reconcile_wikidata.py [--min-mentions N] [--limit N] [--types t1,t2]
```

### 3. `build_registers.py` — registers
Writes the 9 TEI registers to `data/registers/` plus `id_mapping.csv`
(NER UUID → register id) and a reconciliation report. The Wikidata QID is **asserted
only for high/medium** matches; low ones are kept as a curation hint.

### 4. `rewrite_refs.py` — re-pointing the documents
Rewrites the inline `@ref` from NER UUIDs to register ids. **Two invariants** keep the
data safe:

1. **Only `@ref` is rewritten — never `xml:id`.** The mapping is *many-to-one* (the
   deduplication merged several UUIDs into one register entry); rewriting `xml:id` would
   create duplicate ids within a document → invalid XML / indexing errors. The regex is
   anchored on `\sref="…"` precisely for this.
2. **The leading `#` is kept** (`#person-000432`). Consumers strip `^#` for lookup and
   may test `starts-with(@ref, '#work-')`, so the `#` form is the correct, safe one.

Refs absent from the mapping (unreconciled entities) are left verbatim — they keep
pointing at the document's local `standOff`, which stays valid.

```bash
python3 scripts/rewrite_refs.py --dry-run data/*.tei.xml   # count, write nothing
python3 scripts/rewrite_refs.py --out /tmp/preview data/*.tei.xml   # preview elsewhere
python3 scripts/rewrite_refs.py data/*.tei.xml             # in place (atomic, streaming)
```

---

## Configuration

Everything project-specific is an **environment variable** with a sensible default, so
the scripts run on any corpus / layout without edits.

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `TEI_GLOB` | extract | `data/LIV*_reconciled.tei.xml` | Glob of input TEI documents |
| `NER_OUT` | extract, reconcile, build | `build/ner` | Intermediate artifacts + cache dir |
| `REGISTERS_DIR` | build | `data/registers` | Where the 9 registers are written |
| `CORPUS_NAME` | build | `Grand Siecle` | Corpus name injected in register titles |
| `WIKIDATA_CONTACT` | reconcile, build | `set-WIKIDATA_CONTACT-env` | Contact in the User-Agent (**set this**) |
| `WIKIDATA_UA` | reconcile, build | derived | Full User-Agent override |
| `WIKIDATA_API` | reconcile | `https://www.wikidata.org/w/api.php` | API endpoint (e.g. a mirror) |
| `WIKIDATA_MAXLAG` | reconcile | `5` | `maxlag` seconds (WMF politeness) |
| `WIKIDATA_WORKERS` | reconcile | `2` | Parallel API requests (keep low) |

CLI flags (not env): `reconcile_wikidata.py` takes `--min-mentions`, `--limit`,
`--types`; `rewrite_refs.py` takes `--mapping`, `--out`, `--dry-run`.

> **Wikidata politeness:** always set `WIKIDATA_CONTACT` (or `WIKIDATA_UA`). The WMF
> policy expects a descriptive User-Agent with a contact; a missing one risks a 403.

---

## Testing

Offline suite — no network, no dependencies:

```bash
tests/run_tests.sh
```

It runs the `@ref` rewriting invariants (`test_rewrite_refs.py`) and a full offline
`extract → build → rewrite → validate` pipeline on the synthetic fixture
(`test_pipeline.sh`). Add the live Wikidata check with:

```bash
RUN_NETWORK_TESTS=1 WIKIDATA_CONTACT="ci@example.org" tests/run_tests.sh
```

`tests/validate_registers.py` is also usable standalone as a health-check on real output:

```bash
tests/validate_registers.py data/registers --docs "data/*.tei.xml"
```

It verifies well-formedness, the absence of duplicate `xml:id`, `id_mapping` consistency,
and that every document `@ref` resolves (or is a still-unreconciled UUID).

---

## Reconciliation against other referentials

Wikidata is the default authority, but for **art-domain** entities (artworks, materials,
techniques) a specialized thesaurus such as **ArtErm** is more precise. The intended
extension is to add an `idno[@type='arterm']` alongside the Wikidata QID and treat the
art thesaurus as the priority authority for those types. See `docs/workflow.fr.md`.

---

## Provenance

Extracted from the Grand Siècle TEI Publisher application (discourse on painting in
early-17th-century France). The detailed design notes — clustering heuristics, per-type
acceptance rules, anachronism bounds, scoring — live in `docs/workflow.fr.md`.

## License

GNU General Public License v3.0 — see [`LICENSE`](LICENSE).
