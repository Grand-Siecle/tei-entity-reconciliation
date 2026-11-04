#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Surcouche de curation (curation.csv) reappliquee a chaque build.

Les decisions humaines vivent uniquement dans curation.csv, indexees par id de
registre stable :
  ok        : asserte le QID propose quelle que soit sa confiance ("curated")
  corriger  : qid_corrige -> ce QID ("curated") ; vide -> aucun QID ("rejected")
  fusionner : fusion dans fusion_vers (chaines resolues, cycles signales)
  supprimer : entite retiree (ses UUID ne sont plus mappes)
  doute     : aucun changement, note de curation "doute"
label_corrige / type_corrige s'appliquent avec n'importe quelle decision.

Les erreurs ne bloquent jamais le build : elles sont renvoyees en liste.

stdlib uniquement.
"""
import csv
import os
import re

from idregistry import PREFIX

COLUMNS = ["id", "type", "decision", "qid_corrige", "fusion_vers", "label_corrige",
           "type_corrige", "commentaire", "qui", "date", "source"]
DECISIONS = ("ok", "corriger", "fusionner", "supprimer", "doute", "")
QID_RE = re.compile(r"^Q\d+$")
VARIANTS_CAP = 40


def load_curation(path):
    """{id: row} ; {} si le fichier n'existe pas. Cles/valeurs nettoyees."""
    if not os.path.exists(path):
        return {}
    rows = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            row = {k: (r.get(k) or "").strip() for k in COLUMNS}
            row["decision"] = row["decision"].lower()
            if row["id"]:
                rows[row["id"]] = row
    return rows


def save_curation(path, rows):
    """Ecrit curation.csv trie par id (ecriture atomique)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, "") for c in COLUMNS})
    os.replace(tmp, path)


def resolve_fusions(rows, known_ids=None):
    """Lignes 'fusionner' -> ({source: cible finale}, erreurs).

    Les chaines A->B->C donnent A->C et B->C. Auto-fusion, cible vide ou absente
    de known_ids (si fourni) : ligne ignoree et signalee. Cycles : tous leurs
    membres (et ce qui y mene) ignores, une erreur par cycle.
    """
    errors = []
    edges = {}
    for xid in sorted(rows):
        r = rows[xid]
        if r.get("decision") != "fusionner":
            continue
        tgt = (r.get("fusion_vers") or "").strip()
        if not tgt:
            errors.append("%s : fusionner sans fusion_vers (ignore)" % xid)
        elif tgt == xid:
            errors.append("%s : fusion vers soi-meme (ignore)" % xid)
        elif known_ids is not None and tgt not in known_ids:
            errors.append("%s : fusion_vers %s inconnu (ignore)" % (xid, tgt))
        else:
            edges[xid] = tgt
    final, bad, cycles = {}, set(), set()
    for start in sorted(edges):
        path, cur = [], start
        while cur in edges and cur not in path and cur not in final and cur not in bad:
            path.append(cur)
            cur = edges[cur]
        if cur in path:                       # cycle detecte
            cyc = tuple(sorted(path[path.index(cur):]))
            if cyc not in cycles:
                cycles.add(cyc)
                errors.append("cycle de fusion %s (ignore)" % " -> ".join(cyc))
            bad.update(path)
        elif cur in bad:
            bad.update(path)
        else:
            end = final.get(cur, cur)
            for p in path:
                final[p] = end
    return final, errors


def _merge(tgt, src):
    """Fusionne src dans tgt (tgt garde son libelle, son type et son rec)."""
    tgt["mentions"] = tgt.get("mentions", 0) + src.get("mentions", 0)
    if "mentions_raw" in tgt or "mentions_raw" in src:
        tgt["mentions_raw"] = tgt.get("mentions_raw", 0) + src.get("mentions_raw", 0)
    tgt["ner_ids"] = sorted(set(tgt.get("ner_ids", [])) | set(src.get("ner_ids", [])))
    tgt["source_docs"] = sorted(set(tgt.get("source_docs", [])) | set(src.get("source_docs", [])))
    seen, var = {tgt["label"]}, []
    for v in tgt.get("variants", []) + [src["label"]] + src.get("variants", []):
        if v and v not in seen:
            seen.add(v)
            var.append(v)
    tgt["variants"] = var[:VARIANTS_CAP]
    tgt["examples"] = (tgt.get("examples", []) + src.get("examples", []))[:2]


def apply_curation(clusters, rows, registry, fetch_record):
    """Applique curation.csv aux clusters (chacun a deja `xmlid` et `rec`).

    Retourne (clusters_sortie, erreurs). Les clusters d'entree ne sont pas modifies.
    """
    errors = []
    out = []
    for c in clusters:
        c = dict(c)
        for k in ("ner_ids", "source_docs", "variants", "examples"):
            c[k] = list(c.get(k, []))
        c["curation"] = {"decision": "", "qui": "", "date": "", "commentaire": ""}
        c["curation_note"] = ""
        out.append(c)
    by_id = {c["xmlid"]: c for c in out}

    # 1) validation : chaque ligne valide est liee a son cluster
    bound = []   # (cluster, row)
    for xid in sorted(rows):
        r = rows[xid]
        dec = r.get("decision", "")
        if dec and dec not in DECISIONS:
            errors.append("%s : decision inconnue %r (ligne ignoree)" % (xid, dec))
            continue
        c = by_id.get(xid)
        if c is None:
            errors.append("%s : id absent du build (ligne ignoree)" % xid)
            continue
        c["curation"] = {k: r.get(k, "") for k in ("decision", "qui", "date", "commentaire")}
        bound.append((c, r))

    # 2) label_corrige / type_corrige
    removed = set()   # ids des clusters absorbes
    for c, r in bound:
        lab = r.get("label_corrige", "")
        if lab and lab != c["label"]:
            if c["label"] not in c["variants"]:
                c["variants"].insert(0, c["label"])
            c["label"] = lab
        typ = r.get("type_corrige", "")
        if typ and typ != c["type"]:
            if typ not in PREFIX:
                errors.append("%s : type_corrige %r inconnu (ignore)" % (r["id"], typ))
                continue
            old = c["xmlid"]
            other = by_id.get(registry.get(typ, c["label_norm"]))
            if other is None:
                try:
                    new = registry.assign(typ, c["label_norm"], c["ner_ids"])
                except ValueError as e:
                    errors.append("%s : type_corrige impossible (%s)" % (r["id"], e))
                    continue
            registry.mark(old, "deleted")
            c["type"] = typ
            c["orig_xmlid"] = old
            c["rec"] = None          # le match Wikidata visait l'ancien type
            del by_id[old]
            if other is not None:    # la cle existe deja dans le build : on fusionne
                c["xmlid"] = other["xmlid"]
                _merge(other, c)
                removed.add(id(c))
            else:
                c["xmlid"] = new
                by_id[new] = c
    out = [c for c in out if id(c) not in removed]

    # 3) fusions (ids connus = ids apres l'etape 2)
    #    une fusion vers une entite a supprimer ou d'un autre type est refusee
    #    (la source reste intacte) : jamais de fusion-puis-suppression silencieuse
    live = {id(c) for c in out}
    doomed = {c["xmlid"] for c, r in bound
              if id(c) in live and r.get("decision") == "supprimer"}
    frows = {}
    for c, r in bound:
        if id(c) not in live or r.get("decision") != "fusionner":
            continue
        tgt = by_id.get(r.get("fusion_vers", ""))
        if tgt is not None and tgt["xmlid"] in doomed:
            errors.append("%s : fusion_vers %s est a supprimer (fusion ignoree)"
                          % (r["id"], tgt["xmlid"]))
        elif tgt is not None and tgt["type"] != c["type"]:
            errors.append("%s : fusion_vers %s d'un autre type (%s -> %s, fusion ignoree)"
                          % (r["id"], tgt["xmlid"], c["type"], tgt["type"]))
        else:
            frows[c["xmlid"]] = dict(r, id=c["xmlid"])
    fmap, ferr = resolve_fusions(frows, set(by_id))
    errors += ferr
    for src_id in sorted(fmap):
        src, tgt = by_id[src_id], by_id[fmap[src_id]]
        _merge(tgt, src)
        registry.mark(src_id, "merged")
        removed.add(id(src))

    # 4) suppressions
    for c, r in bound:
        if r.get("decision") == "supprimer" and id(c) not in removed:
            registry.mark(c["xmlid"], "deleted")
            removed.add(id(c))
    out = [c for c in out if id(c) not in removed]

    # 5) ok / corriger / doute sur le rec
    for c, r in bound:
        if id(c) in removed:
            continue
        dec = r.get("decision", "")
        rec = c.get("rec")
        if dec == "ok":
            if rec and rec.get("wikidata_qid"):
                c["rec"] = dict(rec, match_confidence="curated")
            else:
                errors.append("%s : ok sans QID propose (rien a asserter)" % r["id"])
        elif dec == "corriger":
            qid = r.get("qid_corrige", "").upper()
            if not qid:
                c["rec"] = None
                c["curation_note"] = "rejected"
            elif not QID_RE.match(qid):
                errors.append("%s : qid_corrige %r invalide (QID rejete)" % (r["id"], qid))
                c["rec"] = None
                c["curation_note"] = "rejected"
            else:
                fetched = fetch_record(c["type"], qid)
                if not fetched:
                    errors.append("%s : %s introuvable sur Wikidata (QID inscrit sans proprietes)"
                                  % (r["id"], qid))
                    fetched = {"wikidata_qid": qid}
                c["rec"] = dict(fetched, match_confidence="curated")
        elif dec == "doute":
            c["curation_note"] = "doute"
    return out, errors
