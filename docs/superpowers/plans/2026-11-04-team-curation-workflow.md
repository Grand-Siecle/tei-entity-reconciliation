# Team Curation Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline incremental and curatable by a non-technical team: stable register ids, a curation overlay re-applied at each build, OpenRefine lot export/import, correct mention counting on the real TEI, local person authority, and the fix for GitHub issue #1 (orphaned in-document standOff lists).

**Architecture:** Keep the four-script shape (extract → reconcile → build → rewrite) and the stdlib-only rule. Add two small modules (`idregistry.py`, `curation.py`) used by `build_registers.py`, and two new CLI scripts (`export_lots.py`, `import_curation.py`) that read/write CSV only. Human decisions live in `curation.csv`; ids live in `id_registry.csv`; both are plain CSV committed to git. `rewrite_refs.py` gains a block transform for `teiHeader`/`standOff` while staying line-streaming for the body.

**Tech Stack:** Python 3.8+ standard library only (`xml.etree`, `csv`, `json`, `urllib`, `unittest`). Bash for the pipeline test.

**Spec:** `docs/superpowers/specs/2026-11-04-team-curation-workflow-spec.md`

## Global Constraints

- Python 3.8+ **standard library only**. No `pip install`. Scripts must run with `python3 scripts/<name>.py` from any cwd.
- Everything project-specific is an **environment variable with a default** (existing table in README): keep `TEI_GLOB`, `NER_OUT`, `REGISTERS_DIR`, `CORPUS_NAME`, `WIKIDATA_*`; add `ENTITIES_DIR`, `PERSONS_CSV`, `ID_REGISTRY`, `CURATION_CSV`, `CORPUS_PUBLISHER`, `CORPUS_LICENCE_STATUS`, `CORPUS_LICENCE_URL`, `BUILD_DATE`.
- Never edit `examples/sample/data/sample_reconciled.tei.xml` (it is the demo). New fixtures go next to it with new names.
- `data/` stays the demo content: nothing from the NAS corpus is committed.
- Existing invariants of `rewrite_refs.py` stay true for the **body**: only `@ref` is rewritten there, leading `#` kept, unmapped refs untouched, streaming.
- Register ids keep the shape `<prefix><6 digits>` with prefixes `person- place- org- work- event- artwork- material- technique- date-`.
- All new tests are `unittest` modules runnable with `python3 -m unittest tests/test_<name>.py -v` **offline** (no network; monkeypatch `api_get`/`wbget`).
- User-facing CSV vocab is French: decisions `ok|corriger|fusionner|supprimer|doute`, status `nouveau|modifié|inchangé`.
- Largest real file is 290 MB: extraction must stay under 4 GB RSS (reference measurement with the current code: 2.3 GB). Do not switch to a DOM-less approach unless needed for that budget.

## Review Focus

1. A TEI file with no `standOff` and no NER `@ref` (real file LIV0004b): extract must count it in “Fichiers traites”, create no entity, no crash; rewrite must leave the file byte-identical. → pinned in Task A (`test_file_without_ner`) and Task E (`test_file_without_standoff_untouched`).
2. A curation CSV edited in Excel: `;` delimiter, UTF-8 BOM, CRLF, trailing empty columns, ids with surrounding spaces → import parses it and finds the ids. → Task D (`test_read_excel_style_csv`).
3. Fusion chain `A→B→C` resolves to C; cycle `A→B→A` is reported and both rows skipped without aborting the build. → Task C (`test_resolve_fusions_chain`, `test_resolve_fusions_cycle`).
4. A cluster whose display label changes between builds (new OCR form wins `best_label`) but shares UUIDs with the previous build keeps its id. → Task C (`test_assign_by_uuid_overlap_when_label_changes`).
5. `rewrite_refs.py` on a document whose `<standOff>` tag is not alone on its line (minified fixture) still rebuilds the lists correctly, and a second run changes nothing. → Task E (`test_minified_standoff_fallback`, `test_second_pass_noop`).

---

## File map

| File | Responsibility |
|---|---|
| `scripts/extract_entities.py` (modify) | doc id, orig/reg counting, word-part join, NER cert, context examples, local PERS/ISNI fields |
| `scripts/reconcile_wikidata.py` (modify) | ISNI-first lookup, modern-label query stage, re-reconcile grown clusters |
| `scripts/idregistry.py` (new) | `IdRegistry`: stable id assignment persisted in `id_registry.csv` |
| `scripts/curation.py` (new) | load/save `curation.csv`, resolve fusions, apply decisions to clusters |
| `scripts/build_registers.py` (modify) | use registry + curation, env header, new notes/idnos, write `entities_registered.json` |
| `scripts/export_lots.py` (new) | lots CSV for OpenRefine/spreadsheet with `statut` |
| `scripts/import_curation.py` (new) | merge reviewer exports into `curation.csv` |
| `scripts/rewrite_refs.py` (modify) | issue #1: rebuild in-document lists (block transform) |
| `tests/validate_registers.py` (modify) | ignore header refs, duplicate xml:id in docs, curation consistency |
| `scripts/slim_cache.py`, `scripts/finalize.sh` (modify) | honour `NER_OUT` |
| `examples/sample/data/sample_origreg.tei.xml` (new) | fixture with orig/reg, word parts, cert, standOff linkGrp, header PERS ref |
| `examples/sample/entities/sample_origreg/entities_persons.csv`, `examples/sample/metadata_personne_sample.csv` (new) | local authority fixtures |
| `tests/test_extract.py`, `tests/test_reconcile.py`, `tests/test_idregistry.py`, `tests/test_curation.py`, `tests/test_export_import.py` (new), `tests/test_rewrite_refs.py` (modify), `tests/test_pipeline.sh` (modify), `tests/run_tests.sh` (modify) | tests |
| `README.md`, `docs/workflow.fr.md` (modify) | docs |

Tasks A, B, C, D, E touch disjoint files and can run in parallel. Task F runs last.

---

### Task A: extract_entities on real TEI (counting, labels, cert, examples, local authority)

**Files:**
- Modify: `scripts/extract_entities.py`
- Create: `examples/sample/data/sample_origreg.tei.xml`, `examples/sample/entities/sample_origreg/entities_persons.csv`, `examples/sample/metadata_personne_sample.csv`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: nothing new.
- Produces, in `entities_clusters.json`, per cluster (all existing keys kept, `mentions` now excludes `<reg>` copies):
  `mentions_raw:int`, `modern_label:str`, `modern_forms:list[str]` (≤10, most common first), `cert:str` (`high|medium|low|""`), `cert_counts:dict`, `examples:list[str]` (≤2, ≤300 chars each), `local_ids:list[str]`, `local_id:str`, `isni:str`, `bnf_ark:str`, `local_label:str`, `local_birth:str`, `local_death:str` (4-digit year or `""`). `source_docs` entries no longer carry `.tei.xml`.
- Produces module functions: `doc_id(path:str, root)->str`, `text_of(el)->str`, `year_of(s:str)->str` (`""` when no 4-digit year), `majority_cert(counts:dict)->str`, `load_local_ids(entities_dir:str, doc:str)->dict`, `load_persons_csv(path:str)->dict`.
- Env: `ENTITIES_DIR` (default: `<dirname(TEI_GLOB)>/entities`), `PERSONS_CSV` (default `""` = skip).

- [ ] **Step 1: Write the fixture `examples/sample/data/sample_origreg.tei.xml`**

Root `<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="SAMPLE2">`. Header with `<author ref="#PERS0001">` and `particDesc/listPerson/person[@xml:id="PERS0001"]`. Body with exactly this content shape (ids are fixed values used by the tests):
- One `<choice>` whose `<orig><s>` holds `<persName resp="#ner-auto" cert="high" ref="#pers-aaaa0001" xml:id="pers-aaaa0001-m1"><w>IOANNEM</w><w>DV</w><w>BRAY</w></persName>` and whose `<reg type="modernized">` holds `<persName ref="#pers-aaaa0001">JOANNEM DU BRAY</persName>`.
- A second `<choice>` where the same person is mentioned again in orig (`xml:id="pers-aaaa0001-m2"`, cert `medium`) and in reg.
- A `<choice>` with a split word inside a mention: `<persName cert="medium" ref="#pers-bbbb0002" xml:id="pers-bbbb0002-m1"><w part="I">Lepi</w><w part="F">dus</w></persName>` in orig (sentence text around it: `<w>vir</w>` before, `<w>fuit</w>` after), with a reg copy `<persName ref="#pers-bbbb0002">Lepidus</persName>`.
- One `<material ref="#mat-cccc0003" xml:id="mat-cccc0003-m1" cert="high"><w>MARMORE</w></material>` in orig, no reg copy.
- `<standOff>` with `listPerson` (`pers-aaaa0001` label `Ioannes Brali`, `pers-bbbb0002` label `Lepidus`), `listPlace` empty is fine, and `<linkGrp type="mentions"><link target="#pers-aaaa0001 #pers-aaaa0001-m1"/><link target="#pers-aaaa0001 #pers-aaaa0001-m2"/><link target="#pers-bbbb0002 #pers-bbbb0002-m1"/></linkGrp>`.

Expected counts after extraction: person `pers-aaaa0001` → mentions 2, mentions_raw 4; `pers-bbbb0002` → mentions 1, mentions_raw 2; material → 1/1.

- [ ] **Step 2: Write the local authority fixtures**

`examples/sample/entities/sample_origreg/entities_persons.csv` (CRLF line endings, `;`):
```
xml_id;canonical_name;mention_count;local_id
pers-aaaa0001;Ioannes Brali;2;PERS0001
pers-bbbb0002;Lepidus;1;
```
`examples/sample/metadata_personne_sample.csv` (UTF-8 **with BOM**, CRLF, `;`, same header as the real file: `BDD;ARK;ISNI;Label_categ;Prenoms;Nom;Sexe;RoleName;GenName;Surnoms;Annee_naissance;ID_Ville_naissance;Ville_naissance;Annee_mort;...` — copy the full real header from the spec) with one row: `PERS0001;ark:/12148/cb12229894r;0000000121296144;AUT;Jean;Du Bray;;;;;15..;;;1629?;...`.

- [ ] **Step 3: Write the failing tests `tests/test_extract.py`**

Use `unittest`; run the script via `subprocess` with `TEI_GLOB`, `NER_OUT=tempdir`, `ENTITIES_DIR=examples/sample/entities`, `PERSONS_CSV=examples/sample/metadata_personne_sample.csv`, then load `entities_clusters.json`. Also import the module (`importlib` from `scripts/`) for unit tests.

```python
def test_mentions_exclude_reg_copies(self):      # pers-aaaa0001: mentions == 2 and mentions_raw == 4
def test_modern_label_and_forms(self):           # modern_label == "JOANNEM DU BRAY"; "JOANNEM DU BRAY" in modern_forms
def test_word_parts_joined(self):                # label of pers-bbbb0002 cluster == "Lepidus" (orig join "Lepidus", not "Lepi dus")
def test_doc_id_from_root_xmlid(self):           # source_docs == ["SAMPLE2"]
def test_doc_id_strips_suffix(self):             # doc_id("x/LIV0010.tei.xml", root_without_id) == "LIV0010"; doc_id("x/LIV0010_reconciled.tei.xml", ...) == "LIV0010"
def test_cert_majority(self):                    # pers-aaaa0001: cert_counts == {"high":1,"medium":1}, cert == "high"; majority_cert({}) == ""
def test_examples_from_orig_sentence(self):      # len(examples)==2 for pers-aaaa0001; "IOANNEM DV BRAY" in examples[0]; pers-bbbb0002 examples[0] == "vir Lepidus fuit"
def test_local_authority_fields(self):           # pers-aaaa0001: local_id=="PERS0001", isni=="0000000121296144", bnf_ark startswith "ark:", local_birth=="" (from "15.."), local_death=="1629"
def test_year_of(self):                          # year_of("1596/03/31")=="1596"; year_of("15..")==""; year_of("1629?")=="1629"; year_of("")==""
def test_file_without_ner(self):                 # a temp TEI with <text><body><p>rien</p></body></text> and no standOff: script exits 0, clusters == [] , stdout contains "Fichiers traites : 1"
def test_demo_fixture_unchanged_behaviour(self): # on sample_reconciled.tei.xml alone: 9 types, pers-0001 cluster mentions == 2 (Platon twice), examples non-empty
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_extract.py -v` — Expected: failures on the new keys / counts.

- [ ] **Step 5: Implement in `scripts/extract_entities.py`**

- `doc_id(path, root)`: root `xml:id` if present else `re.sub(r'(_reconciled)?\.tei\.xml$|\.xml$', '', basename)`.
- Build `parent_map = {c: p for p in root.iter() for c in p}` once per document; `in_reg(el)` walks ancestors looking for local tag `reg`.
- `text_of(el)`: recursive walk over `text`/`tail`; after a `<w>` with `@part` in `{"I","M"}`, whitespace-only pieces are skipped until the next non-blank piece; then collapse whitespace.
- Mention loop: if `in_reg(el)`: add text to `e["modern_forms"]` (Counter) and `mentions_raw += 1`, do not count; else count `mentions += 1`, `mentions_raw += 1`, forms, `cert_counts[el.get("cert")] += 1` when present, and if `len(e["examples"]) < 2`: context = `text_of` of nearest ancestor with local tag in `("s","reg","l","p","ab")` else parent, truncated to 300 chars.
- Cluster merge: sum `mentions_raw`, union `cert_counts`, `examples` = first two across merged ids, `modern_forms` top 10, `modern_label` = most common modern form (`""` if none), `cert = majority_cert(cert_counts)`.
- Local authority: `load_local_ids` reads `<entities_dir>/<doc>/entities_persons.csv` (`encoding="utf-8-sig"`, `newline=""`, `delimiter=";"`), missing file → `{}`. `load_persons_csv` reads with `utf-8-sig`, `;`, keyed by `BDD`, values `label` (`Prenoms Nom`.strip()), `isni`, `ark`, `birth=year_of(Annee_naissance)`, `death=year_of(Annee_mort)`. Cluster: `local_ids` = sorted set over merged ner_ids; `local_id` = the most frequent (ties → first sorted); other fields from the persons dict for that `local_id` (empty strings when unknown).
- `year_of`: first match of `\b(\d{4})\b`, else `""`.
- CSV output: append columns `cert,local_id,isni`.
- Summary print unchanged plus a line `Mentions (hors copies <reg>) : N | brutes : M`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_extract.py -v` — Expected: all PASS. Also run `bash tests/run_tests.sh` — Expected: `ALL TESTS PASSED`.

- [ ] **Step 7: Memory check on the largest real file (local copy)**

Run: `TEI_GLOB="/tmp/claude-1000/-home-rayondemiel-univ-geneve-tei-entity-reconciliation/85046560-fccd-4e1d-858b-95357014a27d/scratchpad/big_tei/LIV*.tei.xml" NER_OUT=/tmp/claude-1000/-home-rayondemiel-univ-geneve-tei-entity-reconciliation/85046560-fccd-4e1d-858b-95357014a27d/scratchpad/big_out2 python3 -c "import resource,runpy; runpy.run_path('scripts/extract_entities.py', run_name='__main__'); print('maxRSS MB', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024)"`
Expected: finishes, `maxRSS MB` < 4096, person cluster count printed close to 1741 and person mentions noticeably below 8612 (reg copies excluded). Report the numbers.

- [ ] **Step 8: Commit**

```bash
git add scripts/extract_entities.py examples/sample tests/test_extract.py
git commit -m "feat(extract): count orig mentions only, modern forms, cert, examples, local PERS authority"
```

---

### Task B: reconcile_wikidata — ISNI first, modern label, re-reconcile grown clusters

**Files:**
- Modify: `scripts/reconcile_wikidata.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: cluster keys from Task A: `isni`, `modern_label`, `mentions`.
- Produces: module functions `isni_wd_format(isni:str)->str`, `wbsearch_isni(isni:str)->list[str]`, `select_pending(work:list, done:dict)->list`; record keys `match_method:str` (`"isni"|"search"`). Confidence `"high"` for ISNI matches.

- [ ] **Step 1: Write the failing tests `tests/test_reconcile.py`**

Import the module from `scripts/`. Monkeypatch `module.api_get` with a fake that dispatches on `params["action"]`/`params.get("list")` and records calls; monkeypatch `module.load_cache/save_cache` to no-ops and reset `module._cache = {}` in `setUp`.

```python
def test_isni_wd_format(self):                 # "0000000121296144" -> "0000 0001 2129 6144"; already spaced input unchanged
def test_wbsearch_isni_query(self):            # fake returns {"query":{"search":[{"title":"Q9191"}]}}; result == ["Q9191"]; recorded params: action=="query", list=="search", srsearch=='haswbstatement:"P213=0000 0001 2129 6144"'
def test_reconcile_one_isni_path(self):        # cluster person label "Descartes", isni set; fake wbgetentities returns entity Q9191 with P31 Q5 + P213; rec["wikidata_qid"]=="Q9191", rec["match_confidence"]=="high", rec["match_method"]=="isni"; no wbsearchentities call recorded
def test_modern_label_stage(self):             # cluster label "CLAVDIVS CRAMOISY", modern_label "CLAUDIUS CRAMOISY", mentions 2, no isni; fake search returns [] for everything; recorded wbsearchentities 'search' values include "CLAUDIUS CRAMOISY" as the 2nd query (after the raw label)
def test_select_pending_redo_grown(self):      # done has ("person","x") with mentions 2 and match_confidence "low"; work cluster mentions 7 -> included; same with done confidence "high" -> excluded; new key -> included; done mentions 6 (already >=5) -> excluded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_reconcile.py -v` — Expected: AttributeError on missing functions.

- [ ] **Step 3: Implement**

- `isni_wd_format`: strip spaces, insert a space every 4 chars.
- `wbsearch_isni`: `api_get({"action":"query","list":"search","srsearch":f'haswbstatement:"P213={isni_wd_format(isni)}"',"srnamespace":0,"srlimit":3}, f"isni::{digits}")` → `[hit["title"] for hit in d["query"]["search"]]` (`[]` on None).
- In `reconcile_one`, before the stages: if `cl.get("isni")`: `ids = wbsearch_isni(...)`; for the first id whose entity passes `accept` → `rec = build_record(...)`, `match_confidence="high"`, `match_method="isni"`, `wikidata_sitelinks`, return. Otherwise fall through.
- Stages: after `(forms[0],"fr")`, insert `(cl["modern_label"],"fr")` when set and `norm(modern_label)` not already in the forms' norms. Records from the search path get `match_method="search"`.
- `select_pending(work, done)`: a cluster is pending when its key is absent from `done`, or when `done[key]["mentions"] < 5 <= cl["mentions"]` and `done[key].get("match_confidence") in (None, "low")`. Use it in `main` (replace the inline comprehension); when a done entry is redone, drop the old record from `results` before appending.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_reconcile.py -v` — Expected: PASS. Run `bash tests/run_tests.sh` — Expected: `ALL TESTS PASSED`.

- [ ] **Step 5: Commit**

```bash
git add scripts/reconcile_wikidata.py tests/test_reconcile.py
git commit -m "feat(reconcile): ISNI-first lookup, modern-label query stage, redo grown low clusters"
```

---

### Task C: stable ids + curation overlay in build_registers

**Files:**
- Create: `scripts/idregistry.py`, `scripts/curation.py`
- Modify: `scripts/build_registers.py`
- Test: `tests/test_idregistry.py`, `tests/test_curation.py`

**Interfaces:**
- Consumes: cluster dicts from Task A (keys `type,label,label_norm,ner_ids,mentions,source_docs,variants,examples,cert,local_id,isni,bnf_ark,local_birth,local_death`); records from `entities_reconciled.json` keyed `(type,label_norm)`.
- Produces:
  - `scripts/idregistry.py`: `PREFIX:dict[str,str]` (type → id prefix, the nine types), `class IdRegistry(path:str)` with `load()->None`, `save()->None`, `assign(typ:str, label_norm:str, ner_ids:list)->str`, `mark(xml_id:str, status:str)->None`, `get(typ:str,label_norm:str)->str|None`, `ids()->set[str]` (all ids, any status). CSV columns `type,label_norm,xml_id,status,ner_ids,created,updated`; `ner_ids` pipe-joined; dates ISO.
  - `scripts/curation.py`: `COLUMNS` (the 11 spec columns), `DECISIONS`, `load_curation(path)->dict[str,dict]` (`{}` when file missing; keys/values stripped; `decision` lowercased), `save_curation(path, rows:dict)->None` (sorted by id), `resolve_fusions(rows)->tuple[dict[str,str], list[str]]` (id → final target; error strings for cycles/unknown targets/self), `apply_curation(clusters:list[dict], rows:dict, registry:IdRegistry, fetch_record)->tuple[list[dict], list[str]]` where each input cluster already has `xmlid` and `rec` (dict or None) and each output cluster has final `type`, `xmlid`, `label`, merged counts, `rec`, `curation:dict` (`decision,qui,date,commentaire`) and `curation_note:str` (`""|"doute"|"rejected"`). `fetch_record(typ:str, qid:str)->dict|None` is injected (build passes the Wikidata fetcher; tests pass a stub).
  - `scripts/build_registers.py`: env `ID_REGISTRY` (default `<REGISTERS_DIR>/id_registry.csv`), `CURATION_CSV` (default `<REGISTERS_DIR>/curation.csv`), `CORPUS_PUBLISHER` (default `"Projet Grand Siècle — UNIL / UNIGE"`), `CORPUS_LICENCE_STATUS` (default `"free"`), `CORPUS_LICENCE_URL` (default CC BY 4.0 URL), `BUILD_DATE` (default `date.today().isoformat()`); `ASSERT_CONF = {"high","medium","curated"}`; writes `<NER_OUT>/entities_registered.json` (list of output clusters: all cluster keys + `xmlid` + `rec` + `curation`); function `fetch_record(typ, qid)` using `reconcile_wikidata.wbget`/`build_record` (import by `sys.path.insert(0, scripts dir)`), returns `None` on failure.

- [ ] **Step 1: Write the failing tests `tests/test_idregistry.py`**

```python
def test_new_ids_sequential_per_type(self):                    # assign person "platon" -> "person-000001"; assign place "athenes" -> "place-000001"; assign person "aristote" -> "person-000002"
def test_same_key_same_id_across_reload(self):                 # assign, save, new IdRegistry(path).load(), assign same key -> same id; ner_ids unioned
def test_assign_by_uuid_overlap_when_label_changes(self):      # row ("person","lomenius",ner_ids [u1,u2]); assign("person","lomenie",[u2,u3]) -> same id, and get("person","lomenie") now returns it
def test_ids_never_reused_after_delete(self):                  # assign a -> 000001, mark deleted, assign b -> 000002
def test_deleted_key_reassigned_reactivates(self):             # mark deleted then assign same key -> same id and status active
def test_type_prefix_unknown_raises(self):                     # assign("unknown",...) raises ValueError
```

- [ ] **Step 2: Write the failing tests `tests/test_curation.py`**

Helper `mk(id, typ, label, mentions, ner_ids, rec=None)` builds a cluster with `xmlid`. Registry = `IdRegistry` on a temp path. `fetch_record` stub returns `{"wikidata_qid": qid, "wikidata_label": "L"+qid, "description": "d"}`.

```python
def test_load_missing_file_is_empty(self)
def test_load_strips_and_lowercases_decision(self)              # row " person-000001 ", "OK" -> key "person-000001", decision "ok"
def test_resolve_fusions_chain(self)                             # A->B, B->C => {A:C, B:C}, no errors
def test_resolve_fusions_cycle(self)                             # A->B, B->A => both absent from map, 1 error mentioning both ids
def test_resolve_fusions_unknown_target_reported(self)           # fusion_vers "person-999999" not in known ids -> error, row skipped
def test_ok_asserts_low_match(self)                              # rec confidence low + decision ok -> rec["match_confidence"]=="curated"
def test_corriger_with_qid_fetches(self)                         # qid_corrige "Q42" -> rec from stub, confidence "curated"
def test_corriger_without_qid_rejects(self)                      # rec becomes None, curation_note == "rejected"
def test_supprimer_drops_cluster(self)                           # not in output; registry status "deleted"
def test_fusionner_merges_counts_and_ids(self)                   # target mentions = sum, ner_ids union, source status "merged", source absent from output
def test_doute_note(self)                                        # curation_note == "doute", rec unchanged
def test_label_and_type_corrige(self)                            # label replaced; type "org"->"person" gives xmlid starting "person-" via registry.assign; old id marked "deleted"; output cluster has orig_xmlid == old id
def test_unknown_decision_reported_and_ignored(self)             # decision "yolo" -> error string, cluster unchanged
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_idregistry.py tests/test_curation.py -v` — Expected: ImportError.

- [ ] **Step 4: Implement `scripts/idregistry.py`**

`assign`: exact key → reactivate if needed, union ner_ids, return. Else among rows of the same type compute overlap of `ner_ids`; best overlap ≥ 1 and its key not yet claimed in this session → rewrite that row's `label_norm` to the new one, return its id. Else new id = prefix + `max(existing numeric for that prefix, 0)+1` zero-padded 6. Keep a `claimed:set` per session to avoid two clusters sharing an id. `save` writes atomically (tmp + `os.replace`).

- [ ] **Step 5: Implement `scripts/curation.py`**

`apply_curation` order: (1) validate decisions, collect errors; (2) `type_corrige`/`label_corrige`; a retyped cluster gets `orig_xmlid`, `registry.mark(old,"deleted")`, `xmlid = registry.assign(new_type, label_norm, ner_ids)`; (3) `resolve_fusions` over rows with decision `fusionner` (known ids = all cluster xmlids after step 2); merge source into target: `mentions`, `mentions_raw` summed, `ner_ids`/`source_docs` union sorted, `variants` = target variants + source label + source variants (dedup, cap 40), `examples` = first two; `registry.mark(source,"merged")`; (4) `supprimer`; (5) `ok|corriger|doute` on `rec`. Return `(clusters, errors)`.

- [ ] **Step 6: Integrate in `scripts/build_registers.py`**

- Replace the rank-based `xmlid` with `registry.assign(typ, c["label_norm"], c["ner_ids"])` for every cluster (including `date`), then `apply_curation`, then group by final type, sort for display by `(-mentions, label)`.
- Print the curation errors (prefixed `! curation:`) and continue.
- `idnos`: when `rec` asserted keep current behaviour; additionally `<idno type="local">{local_id}</idno>` when `c["local_id"]`, and `<idno type="isni">` from `c["isni"]` when no Wikidata ISNI was written.
- `gen_person`: when no asserted rec but `local_birth`/`local_death` → `<birth><date when="…"/></birth>` / `<death>…`.
- `trace_notes`: add `<note type="ner-confidence">{cert}</note>` when `cert`, and `<note type="curation" resp="{qui}">{decision}</note>` when a decision exists (omit `resp` when `qui` empty; also emit `doute`/`rejected` from `curation_note`).
- `header(...)`: publisher/licence/date from env; `<change when="{BUILD_DATE}">`; report line `_Genere le {BUILD_DATE}_`.
- Mapping: for each output cluster, all its `ner_ids` → `xmlid`.
- Write `entities_registered.json` and `registry.save()` at the end.

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_idregistry.py tests/test_curation.py -v` — Expected: PASS. Run `bash tests/run_tests.sh` — Expected: `ALL TESTS PASSED` (pipeline test still passes with ids now coming from a fresh registry in the temp dir; ensure `ID_REGISTRY`/`CURATION_CSV` default under `REGISTERS_DIR` so the test's temp dir isolates them).

- [ ] **Step 8: Commit**

```bash
git add scripts/idregistry.py scripts/curation.py scripts/build_registers.py tests/test_idregistry.py tests/test_curation.py
git commit -m "feat(build): stable register ids (id_registry.csv) and curation overlay (curation.csv)"
```

---

### Task D: export_lots.py and import_curation.py

**Files:**
- Create: `scripts/export_lots.py`, `scripts/import_curation.py`
- Test: `tests/test_export_import.py`

**Interfaces:**
- Consumes: `<NER_OUT>/entities_registered.json` (Task C output: cluster keys from Task A + `xmlid`, `rec`, `curation`), `curation.csv` via `curation.load_curation/save_curation`, `<NER_OUT>/wd_labels.json` (qid → label, may be missing).
- Produces:
  - `export_lots.py`: CLI `--out DIR` (default `<NER_OUT>/lots`), `--types t1,t2`, `--min-mentions N` (default 2), `--max-rows N` (default 500), `--only-new`, `--excel` (`;` + `utf-8-sig`); writes `<type>_lotNN.csv` (NN from 01), `lots_manifest.csv` (`file,type,rows,nouveau,modifié,inchangé`), updates `<NER_OUT>/lots_state.json` (`{id: {"sig": str, "exported": iso-date}}`). Functions: `signature(c:dict)->str` (`f"{label}|{qid}|{mentions}"`), `status_for(xmlid:str, sig:str, state:dict)->str`, `build_rows(registered:list, curation:dict, labels:dict)->list[dict]` (one dict per lot column, order per spec), `write_lots(rows, out_dir, max_rows, excel)->list[str]`.
  - `import_curation.py`: CLI `FILES...`, `--curation PATH` (default `CURATION_CSV` env or `data/registers/curation.csv`), `--qui NAME`, `--known-ids PATH` (an `id_registry.csv`; optional), `--dry-run`. Functions: `read_any_csv(path)->list[dict]` (sniff `,`/`;`/tab, `utf-8-sig`, strips keys and values), `truthy(v)->bool`, `decide_row(row:dict)->dict|None` (returns a curation row or None when nothing decided), `merge_label_groups(decided:list[dict], rows_in:list[dict])->list[dict]`, `merge_into_curation(existing:dict, new_rows:list[dict], source:str, qui:str, date:str)->dict`.

- [ ] **Step 1: Write the failing tests `tests/test_export_import.py`**

Build a small `entities_registered.json` in a temp `NER_OUT` with 3 persons (mentions 10, 3, 1) and 1 place, one person with `rec` (qid Q1, confidence low, alts ["Q2"]), one with `local_id`/`isni`. Then:

```python
def test_lot_columns_and_order(self)               # header == spec column list exactly
def test_min_mentions_filters_singleton(self)      # person with 1 mention absent
def test_status_new_then_unchanged_then_modified(self)  # 1st export all "nouveau"; 2nd export same data all "inchangé"; change a label in registered -> that row "modifié"
def test_split_max_rows(self)                      # --max-rows 2 with 3 rows -> person_lot01.csv (2 rows) + person_lot02.csv (1 row); manifest lists both
def test_prefilled_decision_from_curation(self)    # curation has ok for person-000001 -> row decision == "ok"
def test_excel_mode(self)                          # file starts with BOM, delimiter ';'
def test_lien_and_candidats(self)                  # lien_wikidata == "https://www.wikidata.org/wiki/Q1"; candidats == "Q2"
def test_read_excel_style_csv(self)                # file with BOM, CRLF, ';', trailing empty columns, " person-000001 " -> one row, id stripped
def test_decide_explicit_decision_wins(self)       # decision "corriger", qid_corrige "Q5", qid_final "Q9" -> corriger Q5
def test_decide_qid_final_equal_is_ok(self)        # qid_propose Q1, qid_final Q1 -> ok
def test_decide_qid_final_differs_is_corriger(self)# qid_final Q7 -> corriger qid_corrige Q7
def test_decide_qid_final_empty_rejects(self)      # qid_propose Q1, qid_final "" -> corriger with qid_corrige ""
def test_decide_star_and_flag(self)                # starred "true" -> supprimer; flagged "1" -> doute; both -> supprimer
def test_decide_nothing_returns_none(self)
def test_merge_label_groups(self)                  # two person rows label_corrige "Loménie" (mentions 5 and 2) -> the 2-mention one becomes fusionner -> other's id; the 5-mention keeps label_corrige
def test_merge_into_curation_overrides_and_keeps_others(self)   # existing has A:ok, B:doute; new has A:supprimer -> A supprimer with source/date/qui set, B unchanged
def test_cli_dry_run_writes_nothing(self)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_export_import.py -v` — Expected: ImportError.

- [ ] **Step 3: Implement `scripts/export_lots.py`**

Row values: `label_moderne` = `modern_label`; `variantes` = `"|".join(variants[:12])`; `docs` = `"|".join(source_docs)`; `cert_ner` = `cert`; `qid_propose` etc. from `rec` (empty when None); `confiance` = `rec["match_confidence"]`; `candidats` = `"|".join(wikidata_alts)`; `naissance/deces` = rec birth/death else local; `pays` = `labels.get(country_qid, country_qid)`; curation columns prefilled from `curation.get(xmlid, {})`. Sort rows by `(type, -mentions, label)`. `--only-new` keeps `statut != "inchangé"`. State updated for exported rows only.

- [ ] **Step 4: Implement `scripts/import_curation.py`**

`decide_row` precedence: explicit `decision` → `qid_final` logic → star/flag → `label_corrige`/`type_corrige` alone (decision stays `""` but row is returned). Return rows restricted to `curation.COLUMNS`. `merge_label_groups`: group decided+undecided rows by `(type, normalized label_corrige)` where non-empty; in groups of ≥2, keep the row with max `mentions` (from the lot columns) as target, others become `fusionner`→target unless they already have an explicit decision. Summary printed per decision; `--dry-run` prints and exits 0 without writing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_export_import.py -v` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/export_lots.py scripts/import_curation.py tests/test_export_import.py
git commit -m "feat: export reviewer lots and import curation decisions (OpenRefine/spreadsheet)"
```

---

### Task E: issue #1 — rebuild in-document lists in rewrite_refs; validator updates

**Files:**
- Modify: `scripts/rewrite_refs.py`, `tests/validate_registers.py`, `tests/test_rewrite_refs.py`

**Interfaces:**
- Consumes: `id_mapping.csv` (unchanged format).
- Produces: `rewrite_refs.py` flag `--no-standoff`; functions `transform_block(xml_text:str, mapping:dict)->tuple[str,int]` and `rewrite_document(text:str, mapping:dict, counter:list)->str` (whole-text fallback). `validate_registers.py` flags `--ignore-ref REGEX` (default `^PERS\d+$`), `--curation PATH`; new checks: duplicate `xml:id` inside each document, curation ids/targets exist in registers.

- [ ] **Step 1: Update and extend `tests/test_rewrite_refs.py`**

Keep every existing body assertion. Replace the two standOff assertions by the new contract, and add:
```
ok   standOff person pers-0001 now has xml:id person-00000N (the mapped id)
ok   no duplicate xml:id in output (collect all xml:id values, len == len(set))
ok   unmapped standOff entry (add pers-9998 to a temp copy, absent from the mapping) keeps xml:id pers-9998
ok   two UUIDs mapped to the same id collapse into one entry whose extra name is <persName type="variant">
ok   linkGrp link/@target first token rewritten, mention token (-m1) untouched
ok   --no-standoff keeps old behaviour (xml:id pers-0001 intact)
ok   second pass is a no-op (COUNT 0 and file identical)
ok   minified fixture (whole standOff on one line with the preceding </text>) rebuilt identically to the pretty one
ok   file without standOff and without refs is byte-identical after run (and COUNT 0)
```
Use a temp copy of `sample_reconciled.tei.xml` plus a small inline fixture string for the collapse/linkGrp cases (two persons `pers-0001`, `pers-0003` both mapped to `person-000001` in a temp mapping).

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 tests/test_rewrite_refs.py` — Expected: new assertions FAIL.

- [ ] **Step 3: Implement**

- `transform_block`: `ET.fromstring` (register the TEI namespace with empty prefix so serialization is unprefixed if the block declares it); walk every element with a parent (use a parent map): if `xml:id` in mapping → target id; group consecutive-or-not siblings under the same parent by target; first keeps element, `xml:id = target`; for each other sibling mapped to the same target: for each child whose local tag is in `persName placeName orgName label title objectName name` and whose `text_of` differs from the kept entry's names → deep-copy, set `type="variant"`, append to kept; keep max `cert` by order `high>medium>low`; remove the sibling. Rewrite tokens of `@target` and `@corresp` that start with `#` and whose remainder is a mapped UUID. Return `ET.tostring(root, encoding="unicode")`, count of ids changed.
- `process`: stream lines; when a line's stripped text starts with `<teiHeader` or `<standOff` (and not a self-closing tag), buffer until the line containing the matching close tag, call `transform_block` on the buffer (fall back to the raw buffer if parsing fails, with a warning on stderr), write the result + `"\n"`; other lines go through the existing `REF.sub`. If the opening tag line also contains the closing tag, or `<standOff` appears mid-line (minified), use `rewrite_document` on the whole file instead: regex-locate `<teiHeader…</teiHeader>` and `<standOff…</standOff>` spans, transform each, then `REF.sub` on the rest.
- `--no-standoff` skips the block handling entirely.
- Byte-identical guarantee for untouched files: write the temp only when `counter[0] > 0` or a block changed (reuse the existing rule).
- Docstring: add the issue #1 rationale (in French, same tone as the file).

- [ ] **Step 4: Update `tests/validate_registers.py`**

`check_docs`: skip keys matching `--ignore-ref`; collect `xml:id` values per document with `re.findall(r'xml:id="([^"]+)"')` and FAIL on duplicates (report first 5). `--curation`: every `id` and `fusion_vers` must exist in the register ids set or FAIL.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 tests/test_rewrite_refs.py` — Expected: `0 failure(s)`. Run `bash tests/run_tests.sh` — Expected: `ALL TESTS PASSED`.

- [ ] **Step 6: Real-file smoke test (local copies, no NAS)**

Run `rewrite_refs.py --out <tmp>` with the sample mapping from the earlier test run (`/tmp/claude-1000/-home-rayondemiel-univ-geneve-tei-entity-reconciliation/85046560-fccd-4e1d-858b-95357014a27d/scratchpad/registers/id_mapping.csv`) on `/tmp/claude-1000/-home-rayondemiel-univ-geneve-tei-entity-reconciliation/85046560-fccd-4e1d-858b-95357014a27d/scratchpad/sample_tei/LIV0010.tei.xml`; then `python3 -c "import xml.etree.ElementTree as ET; ET.parse('<out>/LIV0010.tei.xml')"` → no error; `grep -c 'xml:id="person-' <out>/LIV0010.tei.xml` > 0; no duplicate xml:id (`grep -o 'xml:id="[^"]*"' | sort | uniq -d` empty). Report the counts.

- [ ] **Step 7: Commit**

```bash
git add scripts/rewrite_refs.py tests/validate_registers.py tests/test_rewrite_refs.py
git commit -m "fix(rewrite_refs): rebuild in-document standOff/header lists with register ids (closes #1)"
```

---

### Task F: wiring, pipeline test, docs

**Files:**
- Modify: `scripts/slim_cache.py`, `scripts/finalize.sh`, `tests/test_pipeline.sh`, `tests/run_tests.sh`, `README.md`, `docs/workflow.fr.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: `slim_cache.py` and `finalize.sh` honour `NER_OUT`**

`CACHE = os.path.join(os.environ.get("NER_OUT") or os.path.join(ROOT,"build","ner"), "wd_cache.json")`; in the shell script `NER_OUT="${NER_OUT:-build/ner}"` and replace every `build/ner` with `"$NER_OUT"`.

- [ ] **Step 2: Extend `tests/test_pipeline.sh`**

After the existing flow, add in the same temp dirs:
1. Build again with `TEI_GLOB` covering both fixtures (`sample_reconciled` + `sample_origreg`) → assert the register id of the Platon cluster (`pers-0001` in `id_mapping.csv`) is identical to the first build (`ok   ids stable across corpus growth`).
2. Write a `curation.csv` in `REGISTERS_DIR` with: `supprimer` for the Concile event id, `fusionner` Aristote → Platon id, `label_corrige` "Platon (curé)" for Platon, `doute` for Athenes; rebuild → assert: events.xml has no Concile entry, `id_mapping.csv` maps `pers-0002` to Platon's id, persons.xml contains `Platon (curé)`, places.xml contains `<note type="curation">doute</note>` (`ok   curation applied`).
3. `export_lots.py --min-mentions 1 --out $TMP/lots` → `ok   lots exported` (manifest exists, person lot has `statut` column all `nouveau`); run again → all `inchangé`.
4. Write a reviewer file with `id,qid_final` for Platon (`Q859`) and `import_curation.py --dry-run` → `ok   import dry-run`; without dry-run → `curation.csv` now has `corriger` `Q859` for Platon.
5. `validate_registers.py REGISTERS_DIR --docs ... --curation ...` → exit 0.
6. `rewrite_refs.py` on the rewritten copy a second time → `COUNT 0`.

- [ ] **Step 3: `tests/run_tests.sh` runs the new unittest modules**

Add `python3 -m unittest tests/test_extract.py tests/test_reconcile.py tests/test_idregistry.py tests/test_curation.py tests/test_export_import.py` before the pipeline test; fail the suite on non-zero.

- [ ] **Step 4: Run the full suite**

Run: `bash tests/run_tests.sh` — Expected: `ALL TESTS PASSED`.

- [ ] **Step 5: Docs**

README: new scripts in the layout block; configuration table rows for the new env vars; a section **“Team workflow (incremental curation)”** with the cycle (extract → reconcile → build → export lots → reviewers → import → build → rewrite from originals), the two committed files (`id_registry.csv`, `curation.csv`), the decision vocabulary table, and a link to `docs/guide_relecteur_openrefine.md`. `docs/workflow.fr.md`: same in French under “Travail en équipe”, plus a note in Étape 4 describing the issue #1 fix (in-document lists rebuilt, `--no-standoff`), and update the checklist. Keep the existing style.

- [ ] **Step 6: Commit**

```bash
git add scripts/slim_cache.py scripts/finalize.sh tests/test_pipeline.sh tests/run_tests.sh README.md docs/workflow.fr.md
git commit -m "chore: wire new tests, honour NER_OUT everywhere, document the team curation workflow"
```
