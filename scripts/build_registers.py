#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etape 3 du workflow : generation des registres TEI Publisher.

Entrees :
  build/ner/entities_clusters.json    (tous les clusters dedupliques, complet)
  build/ner/entities_reconciled.json  (enrichissement Wikidata, par type+label_norm)

Sorties :
  data/registers/persons.xml        (pb-persons,  listPerson)
  data/registers/places.xml         (pb-places,   listPlace)
  data/registers/organizations.xml  (pb-organizations, listOrg)
  data/registers/works.xml          (pb-works,    listBibl)
  data/registers/events.xml         (pb-events,   listEvent)        [nouveau]
  data/registers/materials.xml      (pb-materials, taxonomy)        [nouveau]
  data/registers/techniques.xml     (pb-techniques, taxonomy)       [nouveau]
  data/registers/artworks.xml       (pb-artworks, listObject)       [nouveau]
  data/registers/dates.xml          (pb-dates, chronologie)         [nouveau]
  data/registers/id_mapping.csv     (uuid NER -> id registre, pour reecrire les @ref)
  build/ner/reconciliation_report.md

Regle de confiance : on ASSERTE le QID Wikidata pour les matchs high/medium
(avec @cert), on conserve les low comme simple piste de curation (note).

stdlib uniquement.
"""
import csv
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Surchargeables par variable d'env (defauts = layout du repo) :
#   NER_OUT       : repertoire des artefacts intermediaires (entrees)
#   REGISTERS_DIR : repertoire de sortie des registres TEI
OUT_DIR = os.environ.get("NER_OUT") or os.path.join(ROOT, "build", "ner")
REG_DIR = os.environ.get("REGISTERS_DIR") or os.path.join(ROOT, "data", "registers")
CLUSTERS = os.path.join(OUT_DIR, "entities_clusters.json")
RECON = os.path.join(OUT_DIR, "entities_reconciled.json")
LABELCACHE = os.path.join(OUT_DIR, "wd_labels.json")

UA = os.environ.get("WIKIDATA_UA") or (
    "tei-entity-reconciliation/1.0 "
    "(https://github.com/GrandSiecle/tei-entity-reconciliation; "
    + os.environ.get("WIKIDATA_CONTACT", "set-WIKIDATA_CONTACT-env") + ")")

# Nom du corpus, insere dans le titre des registres generes (env CORPUS_NAME).
CORPUS = os.environ.get("CORPUS_NAME", "Grand Siecle")
API = "https://www.wikidata.org/w/api.php"

ASSERT_CONF = {"high", "medium"}   # niveaux pour lesquels on inscrit le QID

# ----------------- resolution des labels de QID secondaires -----------------
_lab = {}
GENDER = {"Q6581097": "masculin", "Q6581072": "féminin",
          "Q1097630": "intersexe", "Q1052281": "femme transgenre",
          "Q2449503": "homme transgenre"}

def load_labels():
    global _lab
    if os.path.exists(LABELCACHE):
        try:
            _lab = json.load(open(LABELCACHE, encoding="utf-8"))
        except Exception:
            _lab = {}
    _lab.update(GENDER)

def save_labels():
    json.dump(_lab, open(LABELCACHE, "w", encoding="utf-8"), ensure_ascii=False)

def resolve_labels(qids):
    todo = sorted({q for q in qids if q and q not in _lab})
    for i in range(0, len(todo), 50):
        chunk = todo[i:i+50]
        url = API + "?" + urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(chunk),
            "props": "labels", "languages": "fr|en|la|it", "format": "json"})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8"))
            for q, e in data.get("entities", {}).items():
                L = e.get("labels", {})
                for lg in ("fr", "en", "la", "it"):
                    if lg in L:
                        _lab[q] = L[lg]["value"]; break
            time.sleep(0.1)
        except Exception:
            pass
    save_labels()

def lab(qid):
    return _lab.get(qid, qid)

# ----------------- helpers -----------------
def strip_diac(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def esc(s):
    return escape(s) if s else ""

def date_el(when):
    """<date when=".."/> avec annee paddee a 4 chiffres (ISO ; gere negatif)."""
    if not when:
        return None
    m = re.match(r"(-?)(\d+)(.*)$", when)
    if not m:
        return None
    sign, y, rest = m.groups()
    y = y.zfill(4)
    return f'{sign}{y}{rest}'

# ----------------- en-tete TEI commun -----------------
def header(title, src="Pipeline NER (CamemBERT + GLiNER) + reconciliation Wikidata"):
    return f"""    <teiHeader>
        <fileDesc>
            <titleStmt>
                <title>{esc(title)}</title>
            </titleStmt>
            <publicationStmt>
                <publisher>Projet Grand Siecle — UNIL</publisher>
                <availability status="restricted"><licence target="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</licence></availability>
            </publicationStmt>
            <sourceDesc>
                <p>{esc(src)}</p>
            </sourceDesc>
        </fileDesc>
        <revisionDesc>
            <change when="2026-06-12">Generation automatique depuis les entites NER reconciliees (scripts/build_registers.py)</change>
        </revisionDesc>
    </teiHeader>"""

def wrap(xmlid, body):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="{xmlid}">\n'
            + body + "\n</TEI>\n")

# bloc de tracabilite commun a toutes les entrees
def trace_notes(c, rec):
    out = []
    out.append(f'        <note type="mentions">{c["mentions"]}</note>')
    if c.get("source_docs"):
        out.append(f'        <note type="sources">{esc("|".join(c["source_docs"]))}</note>')
    if rec and rec.get("match_confidence"):
        out.append(f'        <note type="reconciliation-confidence">{rec["match_confidence"]}</note>')
    if rec and rec.get("wikidata_alts"):
        out.append(f'        <note type="wikidata-candidates">{esc("|".join(rec["wikidata_alts"]))}</note>')
    # UUID NER fusionnes -> tracabilite + base du mapping
    out.append(f'        <note type="ner-ids">{esc("|".join(c["ner_ids"]))}</note>')
    return out

def idnos(rec, extra=()):
    out = []
    if rec and rec.get("wikidata_qid") and rec.get("match_confidence") in ASSERT_CONF:
        cert = rec["match_confidence"]
        out.append(f'        <idno type="wikidata" cert="{cert}">{rec["wikidata_qid"]}</idno>')
        for key, typ in (("viaf", "viaf"), ("isni", "isni"), ("gnd", "gnd"),
                         ("bnf", "bnf"), ("lccn", "lccn"), ("geonames", "geonames"),
                         ("aat", "aat")):
            if key in extra and rec.get(key):
                out.append(f'        <idno type="{typ}">{esc(str(rec[key]))}</idno>')
    return out

def _lev(a, b):
    """Distance de Levenshtein (petite, sur des libelles courts)."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j-1] + 1, prev[j-1] + (ca != cb)))
        prev = cur
    return prev[-1]

def related_variant(main, v):
    """Vrai si la variante est une vraie graphie/OCR du label principal, pas un
    token sans rapport agrege par erreur en amont (ex: 'eloquence' pour 'Rome')."""
    a, b = strip_diac(main.lower()).strip(), strip_diac(v.lower()).strip()
    if not b or a == b:
        return False
    # sous-chaine (l'un contient l'autre, tokens partages)
    if a in b or b in a:
        return True
    # prefixe commun significatif
    pre = os.path.commonprefix([a, b])
    if len(pre) >= 4 and len(pre) >= 0.5 * min(len(a), len(b)):
        return True
    # similarite orthographique (OCR) sur le mot le plus long
    L = max(len(a), len(b))
    if L >= 4 and _lev(a, b) <= max(1, int(0.34 * L)):
        return True
    # dernier token partage (ex: "saint Paul" / "Paul")
    if a.split()[-1:] == b.split()[-1:] and len(a.split()[-1]) >= 4:
        return True
    return False

def filtered_variants(c, cap=12):
    """Variantes nettoyees, dedupliquees, reellement apparentees au label."""
    seen, out = set(), []
    for v in c.get("variants", []):
        nv = strip_diac(v.lower())
        if nv in seen or v == c["label"]:
            continue
        if not related_variant(c["label"], v):
            continue
        seen.add(nv)
        out.append(v)
        if len(out) >= cap:
            break
    return out

def variants_named(c, tag, cap=12):
    return [f'        <{tag} type="variant">{esc(v)}</{tag}>'
            for v in filtered_variants(c, cap)]

# ----------------- generateurs par type -----------------
def gen_person(c, rec):
    L = [f'      <person xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    L.append(f'        <persName type="main">{esc(c["label"])}</persName>')
    L.append(f'        <persName type="sort">{esc((rec.get("wikidata_label") if rec else None) or c["label"])}</persName>')
    if rec and rec.get("wikidata_label") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <persName type="standard">{esc(rec["wikidata_label"])}</persName>')
    L += variants_named(c, "persName")
    if rec and rec.get("match_confidence") in ASSERT_CONF:
        b = date_el(rec.get("birth")); d = date_el(rec.get("death"))
        if b: L.append(f'        <birth><date when="{b}"/></birth>')
        if d: L.append(f'        <death><date when="{d}"/></death>')
        if rec.get("gender_qid"): L.append(f'        <sex value="{rec["gender_qid"]}">{esc(lab(rec["gender_qid"]))}</sex>')
        for oq in rec.get("occupation_qids", []):
            L.append(f'        <occupation key="{oq}">{esc(lab(oq))}</occupation>')
        for cq in rec.get("citizenship_qids", []):
            L.append(f'        <nationality key="{cq}">{esc(lab(cq))}</nationality>')
    L += idnos(rec, extra=("viaf", "isni", "gnd", "bnf", "lccn"))
    if rec and rec.get("description") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <note type="description">{esc(rec["description"])}</note>')
    L += trace_notes(c, rec)
    L.append('      </person>')
    return "\n".join(L)

def gen_place(c, rec):
    L = [f'      <place xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    L.append(f'        <placeName type="main">{esc(c["label"])}</placeName>')
    L.append(f'        <placeName type="sort">{esc((rec.get("wikidata_label") if rec else None) or c["label"])}</placeName>')
    if rec and rec.get("wikidata_label") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <placeName type="standard">{esc(rec["wikidata_label"])}</placeName>')
    L += variants_named(c, "placeName")
    asserted = rec and rec.get("match_confidence") in ASSERT_CONF
    if asserted and rec.get("lat") is not None:
        L.append(f'        <location><geo>{rec["lat"]:.5f} {rec["lon"]:.5f}</geo></location>')
    if asserted and rec.get("country_qid"):
        L.append(f'        <country key="{rec["country_qid"]}">{esc(lab(rec["country_qid"]))}</country>')
    L += idnos(rec, extra=("geonames",))
    if asserted and rec.get("description"):
        L.append(f'        <note type="description">{esc(rec["description"])}</note>')
    L += trace_notes(c, rec)
    L.append('      </place>')
    return "\n".join(L)

def gen_org(c, rec):
    L = [f'      <org xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    L.append(f'        <orgName type="main">{esc(c["label"])}</orgName>')
    L.append(f'        <orgName type="sort">{esc((rec.get("wikidata_label") if rec else None) or c["label"])}</orgName>')
    if rec and rec.get("wikidata_label") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <orgName type="standard">{esc(rec["wikidata_label"])}</orgName>')
    L += variants_named(c, "orgName")
    asserted = rec and rec.get("match_confidence") in ASSERT_CONF
    if asserted and rec.get("inception"):
        d = date_el(rec["inception"])
        if d: L.append(f'        <event type="foundation"><date when="{d}"/></event>')
    L += idnos(rec, extra=("isni", "viaf", "gnd"))
    if asserted and rec.get("description"):
        L.append(f'        <note type="description">{esc(rec["description"])}</note>')
    L += trace_notes(c, rec)
    L.append('      </org>')
    return "\n".join(L)

def gen_work(c, rec):
    L = [f'      <bibl xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    L.append(f'        <title type="main">{esc(c["label"])}</title>')
    if rec and rec.get("wikidata_label") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <title type="standard">{esc(rec["wikidata_label"])}</title>')
    L += variants_named(c, "title", cap=8)
    asserted = rec and rec.get("match_confidence") in ASSERT_CONF
    if asserted:
        for aq in rec.get("author_qids", []):
            L.append(f'        <author key="{aq}">{esc(lab(aq))}</author>')
        if rec.get("pub_date"):
            d = date_el(rec["pub_date"])
            if d: L.append(f'        <date type="publication" when="{d}"/>')
        if rec.get("lang_qid"):
            L.append(f'        <textLang key="{rec["lang_qid"]}">{esc(lab(rec["lang_qid"]))}</textLang>')
        for gq in rec.get("genre_qids", []):
            L.append(f'        <note type="genre" key="{gq}">{esc(lab(gq))}</note>')
    L += idnos(rec)
    if asserted and rec.get("description"):
        L.append(f'        <note type="description">{esc(rec["description"])}</note>')
    L += trace_notes(c, rec)
    L.append('      </bibl>')
    return "\n".join(L)

def gen_event(c, rec):
    L = [f'      <event xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    L.append(f'        <label type="main">{esc(c["label"])}</label>')
    if rec and rec.get("wikidata_label") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <label type="standard">{esc(rec["wikidata_label"])}</label>')
    for v in filtered_variants(c, 6):
        L.append(f'        <label type="variant">{esc(v)}</label>')
    asserted = rec and rec.get("match_confidence") in ASSERT_CONF
    if asserted:
        for k, attr in (("when", "when"), ("start", "notBefore"), ("end", "notAfter")):
            if rec.get(k):
                d = date_el(rec[k])
                if d: L.append(f'        <date {attr}="{d}"/>')
        if rec.get("location_qid"):
            L.append(f'        <placeName key="{rec["location_qid"]}">{esc(lab(rec["location_qid"]))}</placeName>')
    L += idnos(rec)
    if asserted and rec.get("description"):
        L.append(f'        <desc>{esc(rec["description"])}</desc>')
    L += trace_notes(c, rec)
    L.append('      </event>')
    return "\n".join(L)

def gen_object(c, rec):  # artwork
    L = [f'      <object xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    L.append(f'        <objectName type="main">{esc(c["label"])}</objectName>')
    if rec and rec.get("wikidata_label") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <objectName type="standard">{esc(rec["wikidata_label"])}</objectName>')
    for v in filtered_variants(c, 6):
        L.append(f'        <objectName type="variant">{esc(v)}</objectName>')
    asserted = rec and rec.get("match_confidence") in ASSERT_CONF
    if asserted:
        for cq in rec.get("class_qids", []):
            L.append(f'        <objectType key="{cq}">{esc(lab(cq))}</objectType>')
    L += idnos(rec, extra=("aat",))
    if asserted and rec.get("description"):
        L.append(f'        <note type="description">{esc(rec["description"])}</note>')
    L += trace_notes(c, rec)
    L.append('      </object>')
    return "\n".join(L)

def gen_category(c, rec):  # material / technique -> taxonomy/category
    L = [f'      <category xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    desc = [f'        <catDesc>']
    desc.append(f'          <term type="main">{esc(c["label"])}</term>')
    if rec and rec.get("wikidata_label") and rec.get("match_confidence") in ASSERT_CONF:
        desc.append(f'          <term type="standard">{esc(rec["wikidata_label"])}</term>')
    for v in filtered_variants(c, 8):
        desc.append(f'          <term type="variant">{esc(v)}</term>')
    desc.append('        </catDesc>')
    L += desc
    L += idnos(rec, extra=("aat",))
    if rec and rec.get("description") and rec.get("match_confidence") in ASSERT_CONF:
        L.append(f'        <note type="description">{esc(rec["description"])}</note>')
    L += trace_notes(c, rec)
    L.append('      </category>')
    return "\n".join(L)

# ----------------- normalisation des dates (chronologie) -----------------
MOIS = {"janvier":1,"fevrier":2,"février":2,"mars":3,"avril":4,"mai":5,"juin":6,
        "juillet":7,"aout":8,"août":8,"septembre":9,"octobre":10,"novembre":11,"decembre":12,"décembre":12}
NUM_ORD = {"premier":1,"deuxieme":2,"troisieme":3,"quatrieme":4,"cinquieme":5,"sixieme":6,
           "septieme":7,"huitieme":8,"neuvieme":9,"dixieme":10,"onzieme":11,"douzieme":12}

def normalize_date(label):
    s = strip_diac(label.lower())
    when = {}
    y = re.search(r"\b(1[4-7]\d{2})\b", s)
    if y:
        when["year"] = y.group(1)
    for name, num in MOIS.items():
        if strip_diac(name) in s:
            when["month"] = num
            break
    return when

def gen_date_item(c, _rec):
    nd = normalize_date(c["label"])
    attrs = ""
    if nd.get("year"):
        w = nd["year"]
        if nd.get("month"):
            w = f'{nd["year"]}-{nd["month"]:02d}'
        attrs = f' when="{w}"'
    L = [f'      <item xml:id="{c["xmlid"]}" n="{c["mentions"]}">']
    L.append(f'        <date{attrs}>{esc(c["label"])}</date>')
    for v in filtered_variants(c, 5):
        L.append(f'        <date type="variant">{esc(v)}</date>')
    L += trace_notes(c, None)
    L.append('      </item>')
    return "\n".join(L)

# ----------------- config par type -----------------
TYPES = {
    "person":    dict(file="persons.xml",       rootid="pb-persons",       prefix="person-",
                      open='  <standOff>\n    <listPerson>', close='    </listPerson>\n  </standOff>',
                      title="Registre des personnes — " + CORPUS, gen=gen_person),
    "place":     dict(file="places.xml",        rootid="pb-places",        prefix="place-",
                      open='  <standOff>\n    <listPlace>', close='    </listPlace>\n  </standOff>',
                      title="Registre des lieux — " + CORPUS, gen=gen_place),
    "org":       dict(file="organizations.xml", rootid="pb-organizations", prefix="org-",
                      open='  <standOff>\n    <listOrg>', close='    </listOrg>\n  </standOff>',
                      title="Registre des organisations — " + CORPUS, gen=gen_org),
    "work":      dict(file="works.xml",         rootid="pb-works",         prefix="work-",
                      open='  <standOff>\n    <listBibl type="work">', close='    </listBibl>\n  </standOff>',
                      title="Registre des oeuvres — " + CORPUS, gen=gen_work),
    "event":     dict(file="events.xml",        rootid="pb-events",        prefix="event-",
                      open='  <standOff>\n    <listEvent>', close='    </listEvent>\n  </standOff>',
                      title="Registre des evenements — " + CORPUS, gen=gen_event),
    "artwork":   dict(file="artworks.xml",      rootid="pb-artworks",      prefix="artwork-",
                      open='  <standOff>\n    <listObject>', close='    </listObject>\n  </standOff>',
                      title="Registre des objets et oeuvres d'art — " + CORPUS, gen=gen_object),
    "material":  dict(file="materials.xml",     rootid="pb-materials",     prefix="material-",
                      open='  <encodingDesc>\n    <classDecl>\n      <taxonomy xml:id="materials">\n      <desc>Materiaux mentionnes dans le corpus</desc>',
                      close='      </taxonomy>\n    </classDecl>\n  </encodingDesc>',
                      title="Registre des materiaux — " + CORPUS, gen=gen_category),
    "technique": dict(file="techniques.xml",    rootid="pb-techniques",    prefix="technique-",
                      open='  <encodingDesc>\n    <classDecl>\n      <taxonomy xml:id="techniques">\n      <desc>Techniques et arts mentionnes dans le corpus</desc>',
                      close='      </taxonomy>\n    </classDecl>\n  </encodingDesc>',
                      title="Registre des techniques — " + CORPUS, gen=gen_category),
    "date":      dict(file="dates.xml",         rootid="pb-dates",         prefix="date-",
                      open='  <standOff>\n    <list type="chronology">', close='    </list>\n  </standOff>',
                      title="Chronologie — " + CORPUS, gen=gen_date_item),
}

def main():
    load_labels()
    clusters = json.load(open(CLUSTERS, encoding="utf-8"))
    recon = {}
    if os.path.exists(RECON):
        for r in json.load(open(RECON, encoding="utf-8")):
            recon[(r["type"], r["label_norm"])] = r

    # collecte des QID secondaires a resoudre en labels
    sec = set()
    for r in recon.values():
        if r.get("match_confidence") not in ASSERT_CONF:
            continue
        for k in ("occupation_qids", "citizenship_qids", "author_qids",
                  "genre_qids", "class_qids", "org_type_qids", "place_type_qids"):
            sec.update(r.get(k, []))
        for k in ("gender_qid", "country_qid", "admin_qid", "lang_qid",
                  "location_qid", "hq_qid"):
            if r.get(k):
                sec.add(r[k])
    print(f"Resolution de {len(sec)} labels de QID secondaires...")
    resolve_labels(sec)

    by_type = defaultdict(list)
    for c in clusters:
        by_type[c["type"]].append(c)

    os.makedirs(REG_DIR, exist_ok=True)
    mapping = []   # (ner_uuid, register_id, type)
    stats = {}

    for typ, cfg in TYPES.items():
        items = sorted(by_type.get(typ, []), key=lambda x: (-x["mentions"], x["label"].lower()))
        # numerotation stable par frequence
        n_match = 0
        bodies = []
        for i, c in enumerate(items, 1):
            c["xmlid"] = f'{cfg["prefix"]}{i:06d}'
            rec = recon.get((typ, c["label_norm"]))
            if rec and rec.get("wikidata_qid") and rec.get("match_confidence") in ASSERT_CONF:
                n_match += 1
            bodies.append(cfg["gen"](c, rec))
            for uuid in c["ner_ids"]:
                mapping.append((uuid, c["xmlid"], typ))
        body = (header(cfg["title"]) + "\n"
                + cfg["open"] + "\n"
                + "\n".join(bodies) + "\n"
                + cfg["close"])
        path = os.path.join(REG_DIR, cfg["file"])
        open(path, "w", encoding="utf-8").write(wrap(cfg["rootid"], body))
        stats[typ] = (len(items), n_match)
        print(f"  {cfg['file']:22s} {len(items):5d} entrees  {n_match:5d} avec QID Wikidata")

    # mapping CSV
    with open(os.path.join(REG_DIR, "id_mapping.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ner_uuid", "register_id", "type"])
        w.writerows(sorted(mapping))
    print(f"  id_mapping.csv         {len(mapping):5d} correspondances UUID->registre")

    # rapport
    write_report(stats, recon, by_type)
    print(f"\nRegistres ecrits dans {REG_DIR}")

def write_report(stats, recon, by_type):
    lines = ["# Rapport de reconciliation NER -> Wikidata\n",
             f"_Genere le 2026-06-12 par scripts/build_registers.py_\n",
             "Le QID Wikidata n'est inscrit dans le registre que pour les matchs "
             "**high** ou **medium**. Les **low** sont conserves comme piste "
             "(`note[@type='wikidata-candidates']`) pour curation manuelle.\n",
             "## Couverture par type\n",
             "| Type | Entites (dedupliquees) | Avec QID Wikidata | Taux |",
             "|------|-----:|-----:|-----:|"]
    for typ, (tot, nm) in stats.items():
        pct = (100*nm/tot) if tot else 0
        lines.append(f"| {typ} | {tot} | {nm} | {pct:.0f}% |")
    # echantillon de matchs high par type
    lines.append("\n## Echantillon de matchs (confiance high)\n")
    for typ in stats:
        recs = [r for (t, _), r in recon.items()
                if t == typ and r.get("match_confidence") == "high" and r.get("wikidata_qid")]
        recs.sort(key=lambda r: -r.get("mentions", 0))
        if not recs:
            continue
        lines.append(f"### {typ}")
        for r in recs[:12]:
            lines.append(f"- **{r['label']}** -> [{r['wikidata_qid']}](https://www.wikidata.org/wiki/{r['wikidata_qid']}) "
                         f"({r.get('wikidata_label','')}) — {r.get('description','') or ''}")
        lines.append("")
    open(os.path.join(OUT_DIR, "reconciliation_report.md"), "w", encoding="utf-8").write("\n".join(lines))

if __name__ == "__main__":
    main()
