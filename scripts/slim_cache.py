#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reduit wd_cache.json : supprime les reponses batch redondantes (g2::) et ne
conserve des entites (e2::) que les champs utiles a la reconciliation.
Conserve tous les appels API deja payes (recherches + entites)."""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "build", "ner", "wd_cache.json")

KEEP_PIDS = {
    "P31","P569","P570","P106","P22","P25","P21","P27",
    "P625","P1566","P17","P131","P571","P159","P112","P488",
    "P50","P577","P136","P1476","P407","P585","P580","P582","P276","P710",
    "P213","P214","P227","P268","P244","P1014","P18","P373",
}

def slim_entity(e):
    if not isinstance(e, dict):
        return e
    out = {}
    # labels / descriptions : garder fr,la,en,it
    for fld in ("labels", "descriptions", "aliases"):
        d = e.get(fld)
        if d:
            out[fld] = {lg: d[lg] for lg in ("fr", "la", "en", "it") if lg in d}
    # compte de sitelinks seulement
    out["_slc"] = len(e.get("sitelinks", {}) or {})
    # claims : seulement les PIDs utiles
    cl = e.get("claims", {})
    out["claims"] = {p: cl[p] for p in KEEP_PIDS if p in cl}
    return out

def main():
    if not os.path.exists(CACHE):
        print("pas de cache"); return
    before = os.path.getsize(CACHE)
    c = json.load(open(CACHE, encoding="utf-8"))
    new = {}
    n_s = n_e = 0
    for k, v in c.items():
        if k.startswith("g2::") or k.startswith("g::"):
            continue                      # batch redondant -> jete
        if k.startswith("e2::") or k.startswith("e::"):
            kk = "e2::" + k.split("::", 1)[1]
            new[kk] = slim_entity(v) if v is not None else None
            n_e += 1
        elif k.startswith("s::"):
            new[k] = v; n_s += 1
        # autres cles ignorees
    tmp = CACHE + ".tmp"
    json.dump(new, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, CACHE)
    after = os.path.getsize(CACHE)
    print(f"cache: {before/1e6:.0f} Mo -> {after/1e6:.0f} Mo | {n_s} recherches, {n_e} entites")

if __name__ == "__main__":
    main()
