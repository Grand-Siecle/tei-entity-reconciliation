#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etape 1 du workflow entites-registres.

Extrait toutes les entites NER des fichiers data/LIV*_reconciled.tei.xml :
  - lit les definitions canoniques du standOff (pers/place/org/event/work/artwork)
  - collecte toutes les mentions inline @ref (y compris mat/tech/date qui n'ont
    pas de liste standOff) pour recuperer formes de surface + comptage + docs source
  - les copies modernisees (<choice><reg>) ne sont pas comptees comme mentions :
    elles alimentent modern_forms / modern_label et mentions_raw
  - deduplique par (type, label_norm) en conservant la trace des UUID NER fusionnes
  - rattache l'autorite locale (PERSnnnn, ISNI, ARK) quand elle est disponible

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
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Surchargeables par variable d'env (defauts = layout du repo) :
#   TEI_GLOB     : motif des fichiers TEI d'entree
#   NER_OUT      : repertoire de sortie des artefacts intermediaires
#   ENTITIES_DIR : autorites locales par document (<doc>/entities_persons.csv)
#   PERSONS_CSV  : metadata_personne.csv (BDD/ARK/ISNI/...) ; vide = ignore
DATA_GLOB = os.environ.get("TEI_GLOB") or os.path.join(ROOT, "data", "LIV*_reconciled.tei.xml")
OUT_DIR = os.environ.get("NER_OUT") or os.path.join(ROOT, "build", "ner")
ENTITIES_DIR = os.environ.get("ENTITIES_DIR") or os.path.join(os.path.dirname(DATA_GLOB), "entities")
PERSONS_CSV = os.environ.get("PERSONS_CSV", "")

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
# ancetres servant de contexte aux exemples (le plus proche gagne)
CONTEXT_TAGS = ("s", "reg", "l", "p", "ab")
EXAMPLE_MAX = 300
CERT_ORDER = ("high", "medium", "low")

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
    """Texte concatene (sans balises) d'un element, nettoye.

    Les mots coupes (<w part="I|M"> suivi de <w part="M|F">) sont recolles :
    apres un <w part="I|M">, les morceaux blancs sont ignores jusqu'au suivant.
    """
    parts = []
    glue = [False]

    def add(t):
        if not t:
            return
        if glue[0]:
            if not t.strip():
                return
            t = t.lstrip()
            glue[0] = False
        parts.append(t)

    def walk(e):
        add(e.text)
        for ch in e:
            walk(ch)
            if local(ch.tag) == "w" and ch.get("part") in ("I", "M"):
                glue[0] = True
            add(ch.tail)

    walk(el)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def local(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def doc_id(path, root):
    """Identifiant de document : @xml:id de la racine, sinon nom de fichier sans suffixe."""
    if root is not None and root.get(XML_ID):
        return root.get(XML_ID)
    return re.sub(r"(_reconciled)?\.tei\.xml$|\.xml$", "", os.path.basename(path))


def year_of(s):
    """Premiere annee a 4 chiffres de s, sinon ""."""
    m = re.search(r"\b(\d{4})\b", s or "")
    return m.group(1) if m else ""


def majority_cert(counts):
    """Valeur de cert la plus frequente ; egalite -> la plus haute ; vide -> ""."""
    if not counts:
        return ""
    rank = {c: i for i, c in enumerate(CERT_ORDER)}
    return min(counts, key=lambda c: (-counts[c], rank.get(c, len(rank)), c))


def load_local_ids(entities_dir, doc):
    """{uuid NER: id local PERS} depuis <entities_dir>/<doc>/entities_persons.csv."""
    if not entities_dir:
        return {}
    path = os.path.join(entities_dir, doc, "entities_persons.csv")
    if not os.path.isfile(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            xid = (row.get("xml_id") or "").strip()
            lid = (row.get("local_id") or "").strip()
            if xid and lid:
                out[xid] = lid
    return out


def load_persons_csv(path):
    """{PERSnnnn: {label, isni, ark, birth, death}} depuis metadata_personne.csv."""
    if not path or not os.path.isfile(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            bdd = (row.get("BDD") or "").strip()
            if not bdd:
                continue
            prenoms = (row.get("Prenoms") or "").strip()
            nom = (row.get("Nom") or "").strip()
            out[bdd] = {
                "label": ("%s %s" % (prenoms, nom)).strip(),
                "isni": (row.get("ISNI") or "").strip(),
                "ark": (row.get("ARK") or "").strip(),
                "birth": year_of(row.get("Annee_naissance") or ""),
                "death": year_of(row.get("Annee_mort") or ""),
            }
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    import xml.etree.ElementTree as ET

    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        print("Aucun fichier TEI trouve sous", DATA_GLOB, file=sys.stderr)
        sys.exit(1)

    persons = load_persons_csv(PERSONS_CSV)

    # uuid NER -> aggregat
    ents = {}  # nerid -> dict(type, forms Counter, docs set, mentions int, canon label, ...)

    def get(nerid, typ):
        e = ents.get(nerid)
        if e is None:
            e = {
                "ner_id": nerid,
                "type": typ,
                "forms": Counter(),
                "docs": set(),
                "mentions": 0,
                "mentions_raw": 0,
                "modern_forms": Counter(),
                "cert_counts": Counter(),
                "examples": [],
                "local_ids": Counter(),
                "canon": None,
            }
            ents[nerid] = e
        return e

    for path in files:
        stem = doc_id(path, None)
        try:
            tree = ET.parse(path)
        except ET.ParseError as ex:
            print(f"  ! parse error {stem}: {ex}", file=sys.stderr)
            continue
        root = tree.getroot()
        doc = doc_id(path, root)
        parent_map = {c: p for p in root.iter() for c in p}

        def in_reg(el):
            p = parent_map.get(el)
            while p is not None:
                if local(p.tag) == "reg":
                    return True
                p = parent_map.get(p)
            return False

        def context_of(el):
            p = parent_map.get(el)
            while p is not None:
                if local(p.tag) in CONTEXT_TAGS:
                    return p
                p = parent_map.get(p)
            return parent_map.get(el)

        # 1) definitions canoniques du standOff
        #    <person xml:id="pers-..."><persName>..</persName></person> etc.
        for el in root.iter():
            xid = el.get(XML_ID)
            if not xid:
                continue
            typ = id_type(xid)
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
        #    une copie dans <reg> (modernisation) n'est pas une mention : elle
        #    ne compte que dans mentions_raw et fournit les formes modernes
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
            e["mentions_raw"] += 1
            txt = text_of(el)
            if in_reg(el):
                if txt:
                    e["modern_forms"][txt] += 1
                continue
            e["mentions"] += 1
            if txt:
                e["forms"][txt] += 1
            cert = el.get("cert")
            if cert:
                e["cert_counts"][cert] += 1
            if len(e["examples"]) < 2:
                ctx = context_of(el)
                if ctx is not None:
                    example = text_of(ctx)[:EXAMPLE_MAX]
                    if example:
                        e["examples"].append(example)

        # 3) autorite locale (PERSnnnn) : dossier nomme d'apres le fichier,
        #    a defaut d'apres l'@xml:id de la racine
        local_map = load_local_ids(ENTITIES_DIR, stem) or load_local_ids(ENTITIES_DIR, doc)
        for nerid, lid in local_map.items():
            if nerid in ents:
                ents[nerid]["local_ids"][lid] += 1

        del parent_map, root, tree

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
                "mentions_raw": 0,
                "modern_forms": Counter(),
                "cert_counts": Counter(),
                "examples": [],
                "local_ids": Counter(),
            }
            clusters[key] = cl
        cl["labels"][label] += max(1, e["mentions"])
        for form, c in e["forms"].items():
            cl["variants"][form] += c
        cl["ner_ids"].append(e["ner_id"])
        cl["docs"] |= e["docs"]
        cl["mentions"] += e["mentions"]
        cl["mentions_raw"] += e["mentions_raw"]
        cl["modern_forms"].update(e["modern_forms"])
        cl["cert_counts"].update(e["cert_counts"])
        cl["examples"].extend(e["examples"][:2 - len(cl["examples"])])
        cl["local_ids"].update(e["local_ids"])

    # ---- materialisation ----
    out = []
    for (typ, nlab), cl in clusters.items():
        display = cl["labels"].most_common(1)[0][0] if cl["labels"] else nlab
        variants = [v for v, _ in cl["variants"].most_common() if v != display]
        modern = [f for f, _ in cl["modern_forms"].most_common(10)]
        # id local le plus frequent ; egalite -> le premier dans l'ordre trie
        lid = ""
        if cl["local_ids"]:
            lid = min(cl["local_ids"].items(), key=lambda kv: (-kv[1], kv[0]))[0]
        pinfo = persons.get(lid, {})
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
            "mentions_raw": cl["mentions_raw"],
            "modern_label": modern[0] if modern else "",
            "modern_forms": modern,
            "cert": majority_cert(cl["cert_counts"]),
            "cert_counts": dict(cl["cert_counts"]),
            "examples": cl["examples"],
            "local_ids": sorted(cl["local_ids"]),
            "local_id": lid,
            "isni": pinfo.get("isni", ""),
            "bnf_ark": pinfo.get("ark", ""),
            "local_label": pinfo.get("label", ""),
            "local_birth": pinfo.get("birth", ""),
            "local_death": pinfo.get("death", ""),
        })

    out.sort(key=lambda x: (x["type"], -x["mentions"], x["label"].lower()))

    with open(os.path.join(OUT_DIR, "entities_clusters.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    with open(os.path.join(OUT_DIR, "entities.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "label", "label_norm", "mentions", "n_ner_ids",
                    "n_docs", "n_variants", "source_docs", "ner_ids",
                    "cert", "local_id", "isni"])
        for e in out:
            w.writerow([e["type"], e["label"], e["label_norm"], e["mentions"],
                        e["n_ner_ids"], len(e["source_docs"]), e["n_variants"],
                        "|".join(e["source_docs"]), "|".join(e["ner_ids"]),
                        e["cert"], e["local_id"], e["isni"]])

    # ---- resume ----
    by_type = Counter(e["type"] for e in out)
    tot_ids = sum(e["n_ner_ids"] for e in out)
    print(f"Fichiers traites : {len(files)}")
    print(f"Entites NER brutes (UUID) : {len(ents)}")
    print(f"Clusters (entites-autorite) : {len(out)}  | UUID couverts : {tot_ids}")
    print(f"Mentions (hors copies <reg>) : {sum(e['mentions'] for e in out)}"
          f" | brutes : {sum(e['mentions_raw'] for e in out)}")
    print("Par type :")
    for t, n in by_type.most_common():
        ment = sum(e["mentions"] for e in out if e["type"] == t)
        print(f"  {t:12s} {n:5d} clusters  {ment:7d} mentions")
    print(f"\nSorties : {OUT_DIR}/entities_clusters.json, entities.csv")


if __name__ == "__main__":
    main()
