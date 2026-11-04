# Spec — Team curation workflow (stable ids, curation overlay, OpenRefine lots) + issue #1

Date: 2026-11-04. Source: discussion with the maintainer after reviewing the real
Grand Siècle corpus (363 NER-annotated TEI files on the institutional NAS).

## Problem

The pipeline works on the real corpus but is not incremental and offers no way for a
non-technical team to curate the results:

1. `build_registers.py` assigns register ids by mention-frequency rank, so ids change
   whenever the corpus changes (131/135 ids changed between a 2-doc and a 3-doc run).
   Any decision keyed on an id, and any document rewritten in place, breaks on the
   next build.
2. `extract_entities.py` counts every mention twice because the real TEI carries each
   mention in `<orig>` (tokenised `<w>`) and again in `<reg type="modernized">`.
   The factor is not constant (Lomenius: 5 real mentions, 9 counted). Doc ids keep
   the `.tei.xml` suffix. Split words (`<w part="I">…</w><w part="F">…</w>`) are
   joined with a space. NER `cert` is ignored. The project's own person authority
   (`entities/<doc>/entities_persons.csv` → `local_id` = `PERSnnnn`;
   `metadata_personne.csv` with ISNI for 416/484 persons) is ignored.
3. There is no curation overlay: human decisions have nowhere to live and no script
   re-applies them.
4. There is no export for reviewers and no import of their work.
5. `tests/validate_registers.py` flags header refs like `#PERS0037` as dangling.
6. `slim_cache.py` and `finalize.sh` ignore `NER_OUT`; `build_registers.py` hardcodes
   the date `2026-06-12`, publisher and `status="restricted"`.
7. GitHub issue #1: after `rewrite_refs.py`, the in-document `standOff`/`particDesc`
   lists keep UUID `xml:id`s while body `@ref`s point to register ids → orphaned
   lists, header↔body joins return nothing.

## Decisions

### Principle
Generated files are disposable and fully regenerated each build. Human decisions live
only in `curation.csv`. The register id is stable and is the key everywhere (curation
file, lots, registers, documents).

### Real-corpus TEI facts the code must handle
- File names `LIVnnnn.tei.xml`, `LIVnnnna.tei.xml`, `LIVnnnn_tN.tei.xml`,
  `LIVnnnn_vN.tei.xml`; root `<TEI xml:id="LIV0010">`.
- Inline mention: `<persName resp="#ner-auto" cert="high|medium|low" ref="#pers-<uuid>"
  xml:id="pers-<uuid>-m1">` with `<w lemma pos msd norm>` children, inside
  `<choice><orig><s>…</s></orig><reg type="modernized">…copy of the mention with plain
  text and no -mN id…</reg></choice>`. Split spans in `<reg>` carry
  `xml:id="ent-xxxx-0" next="#ent-xxxx-1"`.
- `standOff` holds `listPerson/listPlace/listOrg/listBibl/listEvent` with
  `<person xml:id="pers-<uuid>" cert="…"><persName>lemma-based label</persName>`
  and `<linkGrp type="mentions"><link target="#pers-<uuid> #pers-<uuid>-m1"/>`.
- Header: `author ref="#PERS0037"`, `particDesc/listPerson/person[@xml:id="PERS0037"]`
  with `<idno type="isni">`, `<idno type="ark">`, `placeName ref="https://www.geonames.org/…"`.
- Some files have no NER at all (no standOff, no refs): must pass silently.
- Files up to 290 MB; extraction of the largest must stay under 4 GB RSS.
- `entities/<doc>/entities_persons.csv`: `;`-separated, CRLF, columns
  `xml_id;canonical_name;mention_count;local_id`.
- `metadata_personne.csv`: `;`-separated, UTF-8 BOM, CRLF, columns include
  `BDD` (PERS id), `ARK`, `ISNI` (16 digits, no spaces), `Prenoms`, `Nom`,
  `Annee_naissance`, `Annee_mort` (forms like `1596/03/31`, `15..`, `1629?`).

### Files and formats

`id_registry.csv` (default `<REGISTERS_DIR>/id_registry.csv`, committed to git):
`type,label_norm,xml_id,status,ner_ids,created,updated` — status ∈ `active|merged|deleted`.
Ids are never reused. Lookup order: `(type,label_norm)` → majority UUID overlap (same type)
→ new id `<prefix><max+1:06d>`.

`curation.csv` (default `<REGISTERS_DIR>/curation.csv`, committed to git):
`id,type,decision,qid_corrige,fusion_vers,label_corrige,type_corrige,commentaire,qui,date,source`
decision ∈ `ok|corriger|fusionner|supprimer|doute|` (empty = untouched).
- `ok`: assert the proposed QID whatever its confidence (confidence `curated`).
- `corriger`: `qid_corrige` set → use that QID (fetch its properties), confidence
  `curated`; `qid_corrige` empty → no QID (`rejected`).
- `fusionner`: merge into `fusion_vers` (chains resolved, cycles reported and skipped).
- `supprimer`: entity dropped; its UUIDs are not mapped (refs stay local).
- `doute`: no change, `<note type="curation">doute</note>`.
- `label_corrige` / `type_corrige` apply with any decision (or alone).

`entities_registered.json` (in `NER_OUT`, written by build): clusters after curation,
each with `xmlid`, final `type`, `label`, counts, examples, local authority fields and
the reconciliation summary. Input of `export_lots.py`.

Lots: `<out>/<type>_lotNN.csv`, columns in this order:
`id,type,label,label_moderne,variantes,mentions,nb_docs,docs,cert_ner,exemple_1,exemple_2,qid_propose,label_wikidata,description_wikidata,confiance,candidats,lien_wikidata,local_id,isni,naissance,deces,pays,statut,decision,qid_corrige,fusion_vers,label_corrige,type_corrige,commentaire,qui,date`
`statut` ∈ `nouveau|modifié|inchangé` against `<NER_OUT>/lots_state.json`.

Import accepts the lot columns edited in a spreadsheet, and OpenRefine exports with
`qid_final` (from “Add entity identifiers column”), `supprimer`/`starred`,
`doute`/`flagged` (truthy: `true|1|oui|x|yes`), and `label_corrige` (rows of the same
type sharing a non-empty `label_corrige` are merged into the one with most mentions).

### Issue #1 resolution (option 1 of the issue)
`rewrite_refs.py` also rebuilds the in-document lists: any element whose `xml:id` is a
mapped UUID gets the register id; siblings merged into the same id are collapsed into
one entry (first kept, other name-children appended as `type="variant"` when the text
differs, highest `cert` kept); `@target`/`@corresp` tokens that are mapped UUIDs are
rewritten (so `linkGrp/link` stays valid); mention ids `-mN` untouched; unmapped
entries untouched. `--no-standoff` restores the previous behaviour. Running twice is a
no-op. Documented in `docs/workflow.fr.md`.

### Out of scope
Uploading to eXist/TEI Publisher; ArtErm reconciliation; any GUI. The reviewer guide
(`docs/guide_relecteur_openrefine.md` with screenshots) is written by the maintainer's
session, not by the plan tasks, but Task F links it from the README.
