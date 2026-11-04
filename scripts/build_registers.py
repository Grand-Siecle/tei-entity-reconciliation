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
  data/registers/id_registry.csv    (ids stables, jamais reutilises ; env ID_REGISTRY)
  build/ner/entities_registered.json (clusters apres curation, entree de export_lots)
  build/ner/reconciliation_report.md

Ids stables : scripts/idregistry.py (id_registry.csv). Decisions humaines :
curation.csv (env CURATION_CSV), reappliquees a chaque build (scripts/curation.py).

Regle de confiance : on ASSERTE le QID Wikidata pour les matchs high/medium
(avec @cert) et pour les QID valides en curation ("curated", @cert="high") ;
on conserve les low comme simple piste de curation (note).

stdlib uniquement.
"""
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import date
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from idregistry import IdRegistry  # noqa: E402
from curation import apply_curation, load_curation  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Surchargeables par variable d'env (defauts = layout du repo) :
#   NER_OUT       : repertoire des artefacts intermediaires (entrees)
#   REGISTERS_DIR : repertoire de sortie des registres TEI
OUT_DIR = os.environ.get("NER_OUT") or os.path.join(ROOT, "build", "ner")
REG_DIR = os.environ.get("REGISTERS_DIR") or os.path.join(ROOT, "data", "registers")
CLUSTERS = os.path.join(OUT_DIR, "entities_clusters.json")
RECON = os.path.join(OUT_DIR, "entities_reconciled.json")
LABELCACHE = os.path.join(OUT_DIR, "wd_labels.json")
REGISTERED = os.path.join(OUT_DIR, "entities_registered.json")
ID_REGISTRY = os.environ.get("ID_REGISTRY") or os.path.join(REG_DIR, "id_registry.csv")
CURATION_CSV = os.environ.get("CURATION_CSV") or os.path.join(REG_DIR, "curation.csv")

PUBLISHER = os.environ.get("CORPUS_PUBLISHER", "Projet Grand Siècle — UNIL / UNIGE")
LICENCE_STATUS = os.environ.get("CORPUS_LICENCE_STATUS", "free")
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
LICENCE_URL = os.environ.get("CORPUS_LICENCE_URL", CC_BY)
BUILD_DATE = os.environ.get("BUILD_DATE") or date.today().isoformat()

UA = os.environ.get("WIKIDATA_UA") or (
    "tei-entity-reconciliation/1.0 "
    "(https://github.com/GrandSiecle/tei-entity-reconciliation; "
    + os.environ.get("WIKIDATA_CONTACT", "set-WIKIDATA_CONTACT-env") + ")")

# Nom du corpus, insere dans le titre des registres generes (env CORPUS_NAME).
CORPUS = os.environ.get("CORPUS_NAME", "Grand Siecle")
API = "https://www.wikidata.org/w/api.php"

ASSERT_CONF = {"high", "medium", "curated"}   # niveaux pour lesquels on inscrit le QID

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

def esc_attr(s):
    return escape(s, {'"': "&quot;"}) if s else ""

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

def local_date_el(raw):
    """Date de l'autorite locale ("1596/03/31", "1629", "15..", "1629?") ->
    <date when=".."/> si convertible en ISO, sinon <date>texte</date>."""
    m = re.match(r"^(\d{4})(?:[/-](\d{2})(?:[/-](\d{2}))?)?$", raw.strip())
    if m:
        return f'<date when="{"-".join(g for g in m.groups() if g)}"/>'
    return f'<date>{esc(raw)}</date>'

# ----------------- en-tete TEI commun -----------------
def header(title, src="Pipeline NER (CamemBERT + GLiNER) + reconciliation Wikidata"):
    return f"""    <teiHeader>
        <fileDesc>
            <titleStmt>
                <title>{esc(title)}</title>
            </titleStmt>
            <publicationStmt>
                <publisher>{esc(PUBLISHER)}</publisher>
                <availability status="{esc_attr(LICENCE_STATUS)}"><licence target="{esc_attr(LICENCE_URL)}">{"CC BY 4.0" if LICENCE_URL == CC_BY else esc(LICENCE_URL)}</licence></availability>
            </publicationStmt>
            <sourceDesc>
                <p>{esc(src)}</p>
            </sourceDesc>
        </fileDesc>
        <revisionDesc>
            <change when="{BUILD_DATE}">Generation automatique depuis les entites NER reconciliees (scripts/build_registers.py)</change>
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
    if c.get("cert"):
        out.append(f'        <note type="ner-confidence">{esc(c["cert"])}</note>')
    if rec and rec.get("match_confidence"):
        out.append(f'        <note type="reconciliation-confidence">{rec["match_confidence"]}</note>')
    cur = c.get("curation") or {}
    if cur.get("decision"):
        resp = f' resp="{esc_attr(cur["qui"])}"' if cur.get("qui") else ""
        out.append(f'        <note type="curation"{resp}>{esc(cur["decision"])}</note>')
    if c.get("curation_note") and c["curation_note"] != cur.get("decision"):
        out.append(f'        <note type="curation">{esc(c["curation_note"])}</note>')
    if rec and rec.get("wikidata_alts"):
        out.append(f'        <note type="wikidata-candidates">{esc("|".join(rec["wikidata_alts"]))}</note>')
    # NB : la tracabilite UUID NER -> id registre vit dans id_mapping.csv (genere
    # plus bas), pas dans une note interne (UUID inutiles a l'affichage, redondants).
    return out

def idnos(c, rec, extra=()):
    out = []
    wd_isni = False
    if rec and rec.get("wikidata_qid") and rec.get("match_confidence") in ASSERT_CONF:
        # @cert TEI : high|medium|low|unknown -> une assertion curee vaut "high"
        cert = "high" if rec["match_confidence"] == "curated" else rec["match_confidence"]
        out.append(f'        <idno type="wikidata" cert="{cert}">{rec["wikidata_qid"]}</idno>')
        for key, typ in (("viaf", "viaf"), ("isni", "isni"), ("gnd", "gnd"),
                         ("bnf", "bnf"), ("lccn", "lccn"), ("geonames", "geonames"),
                         ("aat", "aat")):
            if key in extra and rec.get(key):
                out.append(f'        <idno type="{typ}">{esc(str(rec[key]))}</idno>')
                wd_isni = wd_isni or key == "isni"
    if c.get("local_id"):
        out.append(f'        <idno type="local">{esc(c["local_id"])}</idno>')
    if c.get("isni") and not wd_isni:
        out.append(f'        <idno type="isni">{esc(c["isni"])}</idno>')
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
    else:   # dates de l'autorite locale faute de match Wikidata asserte
        for tag, key in (("birth", "local_birth"), ("death", "local_death")):
            if c.get(key):
                L.append(f'        <{tag}>{local_date_el(c[key])}</{tag}>')
    L += idnos(c, rec, extra=("viaf", "isni", "gnd", "bnf", "lccn"))
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
    L += idnos(c, rec, extra=("geonames",))
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
    L += idnos(c, rec, extra=("isni", "viaf", "gnd"))
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
    L += idnos(c, rec)
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
    L += idnos(c, rec)
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
    L += idnos(c, rec, extra=("aat",))
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
    L += idnos(c, rec, extra=("aat",))
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

_rw = None   # module reconcile_wikidata, importe a la demande

def fetch_record(typ, qid):
    """Record Wikidata (format reconcile_wikidata.build_record) d'un QID impose
    en curation ; None en cas d'echec (hors ligne, QID inexistant...)."""
    global _rw
    try:
        if _rw is None:
            import reconcile_wikidata
            _rw = reconcile_wikidata
            _rw.load_cache()
        e = _rw.wbget([qid]).get(qid)
        return _rw.build_record(typ, qid, e) if e else None
    except Exception:
        return None

def main():
    load_labels()
    clusters = json.load(open(CLUSTERS, encoding="utf-8"))
    recon = {}
    if os.path.exists(RECON):
        for r in json.load(open(RECON, encoding="utf-8")):
            recon[(r["type"], r["label_norm"])] = r

    # ids stables : ordre (type, frequence, libelle) -> un registre vierge
    # numerote comme avant (par frequence) ; ensuite chaque id reste fixe.
    registry = IdRegistry(ID_REGISTRY, today=BUILD_DATE)
    registry.load()
    clusters = sorted(clusters, key=lambda x: (x["type"], -x["mentions"], x["label"].lower()))
    for c in clusters:
        c["xmlid"] = registry.assign(c["type"], c["label_norm"], c["ner_ids"])
        c["rec"] = recon.get((c["type"], c["label_norm"]))

    # surcouche de curation (decisions humaines)
    rows = load_curation(CURATION_CSV)
    clusters, errors = apply_curation(clusters, rows, registry, fetch_record)
    for e in errors:
        print(f"! curation: {e}")
    if rows:
        print(f"Curation : {len(rows)} lignes lues, {len(errors)} erreurs")
    if _rw is not None:
        _rw.save_cache()

    # collecte des QID secondaires a resoudre en labels
    sec = set()
    for c in clusters:
        r = c["rec"]
        if not r or r.get("match_confidence") not in ASSERT_CONF:
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
        n_match = 0
        bodies = []
        for c in items:
            rec = c["rec"]
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

    with open(REGISTERED, "w", encoding="utf-8") as f:
        json.dump(clusters, f, ensure_ascii=False, indent=1)
    registry.save()
    print(f"  id_registry.csv        {len(registry.ids()):5d} ids ({ID_REGISTRY})")

    # rapport
    write_report(stats, recon, by_type)
    print(f"\nRegistres ecrits dans {REG_DIR}")

def write_report(stats, recon, by_type):
    lines = ["# Rapport de reconciliation NER -> Wikidata\n",
             f"_Genere le {BUILD_DATE} par scripts/build_registers.py_\n",
             "Le QID Wikidata n'est inscrit dans le registre que pour les matchs "
             "**high** ou **medium**, ou valides en curation (**curated**, "
             "curation.csv). Les **low** sont conserves comme piste "
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
