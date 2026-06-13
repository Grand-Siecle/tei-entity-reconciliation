#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etape 1 du workflow entites-registres.

Extrait toutes les entites NER des fichiers data/LIV*_reconciled.tei.xml :
  - lit les definitions canoniques du standOff (pers/place/org/event/work/artwork)
  - collecte toutes les mentions inline @ref (y compris mat/tech/date qui n'ont
    pas de liste standOff) pour recuperer formes de surface + comptage + docs source
  - deduplique par (type, label_norm) en conservant la trace des UUID NER fusionnes

Sortie : build/ner/entities_clusters.json  (un cluster = une entite-autorite candidate)
         build/ner/entities.csv            (format du workflow, lisible)

stdlib uniquement (xml.etree).
"""
import csv
import json
import glob
import os
import re
import sys
import unicodedata
from collections import defaultdict, Counter

TEI = "{http://www.tei-c.org/ns/1.0}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Surchargeables par variable d'env (defauts = layout du repo) :
#   TEI_GLOB : motif des fichiers TEI d'entree
#   NER_OUT  : repertoire de sortie des artefacts intermediaires
DATA_GLOB = os.environ.get("TEI_GLOB") or os.path.join(ROOT, "data", "LIV*_reconciled.tei.xml")
OUT_DIR = os.environ.get("NER_OUT") or os.path.join(ROOT, "build", "ner")

# prefixe d'ID NER -> type logique d'entite
PREFIX_TYPE = {
    "pers": "person",
    "place": "place",
    "org": "org",
    "event": "event",
    "work": "work",
    "artwork": "artwork",
    "mat": "material",
    "tech": "technique",
    "date": "date",
}

# article/particules a retirer en tete pour la normalisation
LEADING = re.compile(
    r"^(le|la|les|l|du|de|des|d|un|une|the|el|il|lo|sainct|saint|sainte|st|ste|s|m|mr|monsieur)\b[\s']*",
    re.IGNORECASE,
)

import ast

def strip_diacritics(s):
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def norm_label(s):
    """Forme normalisee pour deduplication exacte."""
    s = s.lower().strip()
    s = strip_diacritics(s)
    s = s.replace("ſ", "s")           # s long
    s = s.replace("&", " et ")
    s = re.sub(r"[^a-z0-9\s']", " ", s)     # ponctuation -> espace
    # variantes orthographiques modernes vs early-modern
    s = re.sub(r"\s+", " ", s).strip()
    # retire un (et un seul) article/particule de tete
    prev = None
    while prev != s:
        prev = s
        s = LEADING.sub("", s).strip()
    return s


def ref_id(el):
    r = el.get("ref")
    if not r:
        return None
    return r.lstrip("#")


def id_type(idstr):
    if not idstr:
        return None
    m = re.match(r"([a-zA-Z]+)-", idstr)
    if not m:
        return None
    return PREFIX_TYPE.get(m.group(1))


def text_of(el):
    """Texte concatene (sans balises) d'un element inline, nettoye."""
    t = "".join(el.itertext())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def local(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    import xml.etree.ElementTree as ET

    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        print("Aucun fichier TEI trouve sous", DATA_GLOB, file=sys.stderr)
        sys.exit(1)

    # uuid NER -> aggregat
    ents = {}  # nerid -> dict(type, forms Counter, docs set, mentions int, canon label)

    def get(nerid, typ):
        e = ents.get(nerid)
        if e is None:
            e = {
                "ner_id": nerid,
                "type": typ,
                "forms": Counter(),
                "docs": set(),
                "mentions": 0,
                "canon": None,
            }
            ents[nerid] = e
        return e

    for path in files:
        doc = os.path.basename(path).replace("_reconciled.tei.xml", "")
        try:
            tree = ET.parse(path)
        except ET.ParseError as ex:
            print(f"  ! parse error {doc}: {ex}", file=sys.stderr)
            continue
        root = tree.getroot()

        # 1) definitions canoniques du standOff
        #    <person xml:id="pers-..."><persName>..</persName></person> etc.
        for el in root.iter():
            xid = el.get("{http://www.w3.org/XML/1998/namespace}id")
            if not xid:
                continue
            typ = id_type(xid)
            if typ is None or local(el.tag) in ("persName",):
                # on ne veut que les conteneurs (person/place/org/event/bibl/object)
                pass
            # conteneurs canoniques : person, place, org, event, bibl, object
            ln = local(el.tag)
            if ln in ("person", "place", "org", "event", "bibl", "object") and typ:
                e = get(xid, typ)
                e["docs"].add(doc)
                # libelle canonique = 1er enfant nom
                namechild = None
                for ch in el:
                    if local(ch.tag) in ("persName", "placeName", "orgName",
                                          "label", "title", "objectName", "name"):
                        namechild = ch
                        break
                if namechild is not None:
                    label = text_of(namechild)
                    if label:
                        e["canon"] = label
                        e["forms"][label] += 0  # connu mais comptage via inline

        # 2) mentions inline avec @ref
        for el in root.iter():
            r = ref_id(el)
            if not r:
                continue
            typ = id_type(r)
            if typ is None:
                continue
            ln = local(el.tag)
            if ln not in ("persName", "placeName", "orgName", "rs", "date",
                          "name", "term", "object", "bibl",
                          "material", "title", "objectName"):
                continue
            e = get(r, typ)
            e["docs"].add(doc)
            e["mentions"] += 1
            txt = text_of(el)
            if txt:
                e["forms"][txt] += 1

    # ---- reassemblage des spans splittes (ent-xxxx-0/-1 partagent le meme @ref) ----
    # deja gere : ils partagent le meme ref donc agreges ensemble.

    # ---- choix du meilleur libelle d'affichage par entite ----
    def best_label(e):
        cand = Counter()
        for form, c in e["forms"].items():
            cand[form] += c
        if e["canon"]:
            cand[e["canon"]] += 0
        if not cand:
            return e["canon"] or ""
        def score(item):
            form, freq = item
            s = freq * 2.0
            if form and form[0].isupper():
                s += 3
            if " " in form:
                s += 2
            if len(form) >= 4:
                s += 1
            if len(form) <= 2:
                s -= 5
            # penalise tout-minuscule court
            if form.islower() and len(form) < 5:
                s -= 2
            return s
        return max(cand.items(), key=score)[0]

    # ---- clustering inter-entites par (type, label_norm) ----
    clusters = {}  # (type, norm) -> cluster
    for e in ents.values():
        label = best_label(e)
        if not label:
            continue
        nlab = norm_label(label)
        if not nlab:
            continue
        key = (e["type"], nlab)
        cl = clusters.get(key)
        if cl is None:
            cl = {
                "type": e["type"],
                "label_norm": nlab,
                "labels": Counter(),
                "variants": Counter(),
                "ner_ids": [],
                "docs": set(),
                "mentions": 0,
            }
            clusters[key] = cl
        cl["labels"][label] += max(1, e["mentions"])
        for form, c in e["forms"].items():
            cl["variants"][form] += c
        cl["ner_ids"].append(e["ner_id"])
        cl["docs"] |= e["docs"]
        cl["mentions"] += e["mentions"]

    # ---- materialisation ----
    out = []
    for (typ, nlab), cl in clusters.items():
        display = cl["labels"].most_common(1)[0][0] if cl["labels"] else nlab
        variants = [v for v, _ in cl["variants"].most_common() if v != display]
        out.append({
            "type": typ,
            "label": display,
            "label_norm": nlab,
            "variants": variants[:40],
            "n_variants": len(cl["variants"]),
            "ner_ids": sorted(cl["ner_ids"]),
            "n_ner_ids": len(cl["ner_ids"]),
            "source_docs": sorted(cl["docs"]),
            "mentions": cl["mentions"],
        })

    out.sort(key=lambda x: (x["type"], -x["mentions"], x["label"].lower()))

    with open(os.path.join(OUT_DIR, "entities_clusters.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    with open(os.path.join(OUT_DIR, "entities.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "label", "label_norm", "mentions", "n_ner_ids",
                    "n_docs", "n_variants", "source_docs", "ner_ids"])
        for e in out:
            w.writerow([e["type"], e["label"], e["label_norm"], e["mentions"],
                        e["n_ner_ids"], len(e["source_docs"]), e["n_variants"],
                        "|".join(e["source_docs"]), "|".join(e["ner_ids"])])

    # ---- resume ----
    by_type = Counter(e["type"] for e in out)
    tot_ids = sum(e["n_ner_ids"] for e in out)
    print(f"Fichiers traites : {len(files)}")
    print(f"Entites NER brutes (UUID) : {len(ents)}")
    print(f"Clusters (entites-autorite) : {len(out)}  | UUID couverts : {tot_ids}")
    print("Par type :")
    for t, n in by_type.most_common():
        ment = sum(e["mentions"] for e in out if e["type"] == t)
        print(f"  {t:12s} {n:5d} clusters  {ment:7d} mentions")
    print(f"\nSorties : {OUT_DIR}/entities_clusters.json, entities.csv")


if __name__ == "__main__":
    main()
