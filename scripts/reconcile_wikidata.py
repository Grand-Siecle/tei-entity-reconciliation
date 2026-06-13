#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etape 2 du workflow : reconciliation Wikidata des clusters d'entites.

Lit build/ner/entities_clusters.json, interroge l'API Wikidata
(wbsearchentities + wbgetentities) et valide chaque match par les PROPRIETES
attendues selon le type (un lieu a des coordonnees/geonames, une personne a des
dates de naissance/mort ou une occupation, etc.) pour eviter les faux positifs.

Resumable : cache disque (build/ner/wd_cache.json) + ecriture incrementale de
build/ner/entities_reconciled.json. Relancable sans tout refaire.

Usage :
  python3 scripts/reconcile_wikidata.py [--min-mentions N] [--limit N] [--types t1,t2]

stdlib uniquement (urllib).
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.environ.get("NER_OUT") or os.path.join(ROOT, "build", "ner")
CLUSTERS = os.path.join(OUT_DIR, "entities_clusters.json")
CACHE = os.path.join(OUT_DIR, "wd_cache.json")
OUT = os.path.join(OUT_DIR, "entities_reconciled.json")

# User-Agent conforme a la politique WMF :
# "ToolName/version (URL; email) library/version"  (sinon risque de blocage 403).
# Surcharger via WIKIDATA_UA, ou au moins fournir un contact via WIKIDATA_CONTACT.
UA = os.environ.get("WIKIDATA_UA") or (
    "tei-entity-reconciliation/1.0 "
    "(https://github.com/GrandSiecle/tei-entity-reconciliation; "
    + os.environ.get("WIKIDATA_CONTACT", "set-WIKIDATA_CONTACT-env") + ") "
    "python-urllib")
API = os.environ.get("WIKIDATA_API") or "https://www.wikidata.org/w/api.php"
# maxlag : recommande pour les taches non-interactives ; l'API renvoie une erreur
# si la replication DB est en retard de plus de N secondes -> on patiente.
MAXLAG = int(os.environ.get("WIKIDATA_MAXLAG", "5"))
# nb de requetes paralleles vers l'API (politesse WMF : rester bas).
MAX_WORKERS = int(os.environ.get("WIKIDATA_WORKERS", "2"))

# langues de recherche par ordre de priorite (corpus FR + sources latines)
SEARCH_LANGS = ["fr", "la", "en", "it"]

# proprietes Wikidata recoltees
PROPS = {
    "P569": "birth", "P570": "death", "P106": "occupation", "P21": "gender",
    "P27": "country_citizen", "P21": "gender",
    "P625": "coord", "P17": "country", "P1566": "geonames", "P131": "admin",
    "P571": "inception", "P159": "hq", "P112": "founder",
    "P50": "author", "P577": "pub_date", "P136": "genre", "P407": "lang",
    "P585": "point_in_time", "P580": "start", "P582": "end",
    "P213": "isni", "P214": "viaf", "P227": "gnd", "P244": "lccn",
    "P268": "bnf", "P1014": "aat", "P18": "image", "P373": "commons",
}

# acceptation par type : fonction(claims_pset) -> bool
def has(c, *pids):
    return any(p in c for p in pids)

def accept_person(c, p31):
    if "Q5" in p31:  # human
        return True
    # deites / figures bibliques / personnages : Q178885 deity, Q20643955 human biblical figure
    if p31 & {"Q178885", "Q20643955", "Q3512563", "Q51626", "Q22989102"}:
        return True
    # signal fort : dates de naissance/mort ou occupation
    return has(c, "P569", "P570") or has(c, "P106", "P22", "P25")

def accept_place(c, p31):
    return has(c, "P625", "P1566", "P17", "P131")  # coord / geonames / pays / admin

def accept_org(c, p31):
    if has(c, "P571", "P159", "P112", "P488"):     # inception/hq/founder/chair
        return True
    # exclut clairement personnes & lieux
    if has(c, "P569", "P570", "P625"):
        return False
    return bool(p31)  # un P31 quelconque (verifie cote requete par type)

def accept_work(c, p31):
    return has(c, "P50", "P577", "P136", "P1476", "P407")

def accept_material(c, p31):
    return has(c, "P1014") or bool(p31)            # AAT ou tout P31 (materiaux peu ambigus)

def accept_technique(c, p31):
    return has(c, "P1014") or bool(p31)

def accept_event(c, p31):
    return has(c, "P585", "P580", "P582", "P276", "P710")

ACCEPT = {
    "person": accept_person, "place": accept_place, "org": accept_org,
    "work": accept_work, "material": accept_material, "technique": accept_technique,
    "artwork": accept_material, "event": accept_event,
}

# ---------------- HTTP avec cache & retries ----------------
import threading
_cache = {}
_clock = threading.Lock()

def load_cache():
    global _cache
    if os.path.exists(CACHE):
        try:
            _cache = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            _cache = {}

def save_cache():
    # snapshot sous lock : sinon un thread worker peut muter _cache pendant la
    # serialisation -> RuntimeError: dictionary changed size during iteration
    with _clock:
        snap = dict(_cache)
    tmp = CACHE + ".tmp"
    json.dump(snap, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, CACHE)

def api_get(params, ck):
    with _clock:
        if ck in _cache:
            return _cache[ck]
    params = dict(params); params["format"] = "json"
    params["maxlag"] = MAXLAG
    url = API + "?" + urllib.parse.urlencode(params)
    data = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8"))
            # erreur applicative maxlag (serveur surcharge) -> on patiente et on reessaie
            if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag":
                data = None
                time.sleep(2.0 * (attempt + 1))
                continue
            time.sleep(0.02)
            break
        except urllib.error.HTTPError as e:
            # 429 Too Many Requests / 503 : honorer Retry-After si fourni
            if e.code in (429, 503):
                ra = e.headers.get("Retry-After")
                wait = float(ra) if (ra and ra.isdigit()) else 1.5 * (attempt + 1)
                time.sleep(min(wait, 10))
            else:
                time.sleep(1.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1.0 * (attempt + 1))
        except json.JSONDecodeError:
            time.sleep(0.8)
    with _clock:
        _cache[ck] = data
    return data

def wbsearch(text, lang):
    d = api_get({"action": "wbsearchentities", "search": text[:200],
                 "language": lang, "uselang": lang, "limit": 7, "type": "item"},
                f"s::{lang}::{text.lower()}")
    if not d:
        return []
    return [h["id"] for h in d.get("search", [])]

# champs claims conserves (memes que slim_cache.py) pour limiter la RAM/disque
_KEEP_PIDS = {
    "P31","P569","P570","P106","P22","P25","P21","P27",
    "P625","P1566","P17","P131","P571","P159","P112","P488",
    "P50","P577","P136","P1476","P407","P585","P580","P582","P276","P710",
    "P213","P214","P227","P268","P244","P1014","P18","P373",
}

def slim_entity(e):
    """Ne garde que les champs utiles -> cache et RAM ~10x plus petits."""
    if not isinstance(e, dict):
        return e
    if "_slc" in e:        # deja slim
        return e
    out = {}
    for fld in ("labels", "descriptions", "aliases"):
        d = e.get(fld)
        if d:
            out[fld] = {lg: d[lg] for lg in ("fr", "la", "en", "it") if lg in d}
    out["_slc"] = len(e.get("sitelinks", {}) or {})
    cl = e.get("claims", {})
    out["claims"] = {p: cl[p] for p in _KEEP_PIDS if p in cl}
    return out

def wbget(qids):
    """Fetch entities (<=50) -> {qid: entity}. Cached per qid via combined key."""
    out = {}
    todo = []
    with _clock:
        for q in qids:
            ck = f"e2::{q}"
            if ck in _cache:
                if _cache[ck] is not None:
                    out[q] = _cache[ck]
            else:
                todo.append(q)
    for i in range(0, len(todo), 50):
        chunk = todo[i:i+50]
        d = api_get({"action": "wbgetentities", "ids": "|".join(chunk),
                     "props": "labels|descriptions|claims|aliases|sitelinks",
                     "languages": "fr|la|en|it"},
                    "g2::" + "|".join(chunk))
        ents = (d or {}).get("entities", {})
        with _clock:
            for q in chunk:
                e = ents.get(q)
                e = slim_entity(e) if e is not None else None
                _cache[f"e2::{q}"] = e
                if e is not None:
                    out[q] = e
    return out

# ---------------- helpers d'extraction ----------------
def strip_diac(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def norm(s):
    s = strip_diac(s.lower()).replace("ſ", "s")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def early_modern_variants(label):
    """Genere des variantes pour matcher la graphie ancienne / OCR."""
    out = {label}
    base = label.strip().strip(".,;:")
    out.add(base)
    # u/v, i/j, long-s, doublons latins
    v = base
    for a, b in [("v", "u"), ("u", "v"), ("j", "i"), ("i", "j"), ("ç", "c"), ("œ", "oe")]:
        out.add(v.replace(a, b))
    # retire titres religieux
    out.add(re.sub(r"^(s\.?|st\.?|sainct|saint|sainte|ste)\s+", "", base, flags=re.I))
    # latinise terminaisons frequentes
    if base.lower().endswith("e"):
        out.add(base[:-1] + "us")
    out = {o for o in out if len(o) >= 3}
    return list(out)[:6]

def claims_pset(entity):
    """-> (set des PIDs presents, set des QIDs de P31)."""
    claims = entity.get("claims", {})
    pset = set(claims.keys())
    p31 = set()
    for st in claims.get("P31", []):
        try:
            p31.add(st["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            pass
    return pset, p31

def get_qid_claim(entity, pid):
    for st in entity.get("claims", {}).get(pid, []):
        try:
            return st["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
    return None

def get_qid_claims(entity, pid):
    out = []
    for st in entity.get("claims", {}).get(pid, []):
        try:
            out.append(st["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    return out

def get_time(entity, pid):
    for st in entity.get("claims", {}).get(pid, []):
        try:
            return st["mainsnak"]["datavalue"]["value"]["time"]
        except (KeyError, TypeError):
            continue
    return None

def get_str(entity, pid):
    for st in entity.get("claims", {}).get(pid, []):
        try:
            return st["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
    return None

def get_coord(entity):
    for st in entity.get("claims", {}).get("P625", []):
        try:
            v = st["mainsnak"]["datavalue"]["value"]
            return v["latitude"], v["longitude"]
        except (KeyError, TypeError):
            continue
    return None

def label_of(entity, langs=("fr", "la", "en", "it")):
    L = entity.get("labels", {})
    for lg in langs:
        if lg in L:
            return L[lg]["value"]
    return next(iter(L.values()), {}).get("value") if L else None

def desc_of(entity, langs=("fr", "en", "la", "it")):
    D = entity.get("descriptions", {})
    for lg in langs:
        if lg in D:
            return D[lg]["value"]
    return None

def fmt_time(t):
    """+1597-11-03T00:00:00Z -> 1597-11-03 ; gere annees negatives & precision."""
    if not t:
        return None
    m = re.match(r"([+-])(\d+)-(\d\d)-(\d\d)", t)
    if not m:
        return None
    sign, y, mo, d = m.groups()
    y = int(y)
    year = ("-" if sign == "-" else "") + str(y)
    if mo == "00":
        return year
    if d == "00":
        return f"{year}-{mo}"
    return f"{year}-{mo}-{d}"

def clean_query(label):
    """Forme principale nettoyee : retire titres religieux/honorifiques + ponctuation."""
    s = label.strip().strip(".,;:'\"")
    s = re.sub(r"^(s\.?|st\.?|ste\.?|sainct[e]?|saint[e]?|monsieur|mr\.?|le sieur|sieur)\s+",
               "", s, flags=re.I)
    return s.strip()

def birth_year(e):
    t = get_time(e, "P569") or get_time(e, "P570")
    if not t:
        return None
    m = re.match(r"([+-])(\d+)-", t)
    if not m:
        return None
    y = int(m.group(2))
    return -y if m.group(1) == "-" else y

def sitelink_count(e):
    if "_slc" in e:
        return e["_slc"]
    return len(e.get("sitelinks", {}) or {})

# pays/entites plausibles pour un corpus francais du XVIIe (Europe + monde antique
# mediterraneen + Proche-Orient biblique). Sert a demoter les homonymes du Nouveau
# Monde / Oceanie (ex: Port Royal en Jamaique vs abbaye de Port-Royal).
PLAUSIBLE_PLACE_Q = {
    "Q142",   # France
    "Q38",    # Italie
    "Q41",    # Grece
    "Q43",    # Turquie
    "Q801",   # Israel
    "Q219060", # Palestine
    "Q79",    # Egypte
    "Q183",   # Allemagne
    "Q29",    # Espagne
    "Q145",   # Royaume-Uni
    "Q237",   # Vatican
    "Q31",    # Belgique
    "Q55",    # Pays-Bas
    "Q39",    # Suisse
    "Q45",    # Portugal
    "Q40",    # Autriche
    "Q36",    # Pologne
    "Q213",   # Tchequie
    "Q28",    # Hongrie
    "Q224",   # Croatie
    "Q221",   # Macedoine du Nord
    "Q2277",  # Empire romain
    "Q12544", # Empire byzantin
    "Q83958", # Republique romaine
    "Q1747689", # Grece antique
    "Q3024240", # Empire ottoman (hist)
    "Q200464",  # Mesopotamie
    "Q5743",  # Syrie (region)
    "Q858",   # Syrie
    "Q796",   # Irak
    "Q810",   # Jordanie
}

def place_geo_bonus(e):
    """+1 si le pays est plausible, -3 si un pays est connu mais hors zone (Nouveau Monde...)."""
    c = get_qid_claim(e, "P17")
    if not c:
        return 0
    return 1 if c in PLAUSIBLE_PLACE_Q else -3

# ---------------- coeur : reconcilier un cluster ----------------
def score_candidate(typ, nlabel, qid, e, rank, accept):
    """Evalue un candidat -> (tuple_score, fame) ou None si rejete."""
    pset, p31 = claims_pset(e)
    if not accept(pset, p31):
        return None
    # filtre d'anachronisme : corpus du XVIIe. Une personne nee apres ~1660 ou un
    # evenement date apres ~1700 ne peut pas etre celui du texte (ex: "Concile" ne
    # doit pas matcher Vatican II 1962).
    if typ == "person":
        by = birth_year(e)
        if by is not None and by > 1660:
            return None
    if typ == "event":
        ey = event_year(e)
        if ey is not None and ey > 1700:
            return None
    names = [label_of(e) or ""]
    for lg in ("fr", "la", "en", "it"):
        for a in e.get("aliases", {}).get(lg, []):
            names.append(a["value"])
    nnames = [norm(n) for n in names if n]
    if nlabel in nnames:
        sim = 3
    elif any((nlabel in n or n in nlabel) for n in nnames):
        sim = 2
    elif any(nlabel.split()[-1:] == n.split()[-1:] for n in nnames):
        sim = 1
    else:
        return None
    fame = sitelink_count(e)
    geo = place_geo_bonus(e) if typ == "place" else 0
    score_fame = min(fame, 60) + geo * 12
    return (sim, score_fame, -rank), fame

def event_year(e):
    """Annee d'un evenement (point-in-time / debut)."""
    t = get_time(e, "P585") or get_time(e, "P580") or get_time(e, "P582")
    if not t:
        return None
    mm = re.match(r"([+-])(\d+)-", t)
    if not mm:
        return None
    y = int(mm.group(2))
    return -y if mm.group(1) == "-" else y

def reconcile_one(cl):
    typ = cl["type"]
    if typ == "date":
        return None
    label = cl["label"]
    accept = ACCEPT.get(typ, lambda c, p: bool(p))
    nlabel = norm(label)
    m = cl["mentions"]

    clean = clean_query(label)
    forms = []
    for q in (label, clean):
        if q and norm(q) not in {norm(x) for x in forms}:
            forms.append(q)
    # variantes (graphie ancienne / OCR) seulement pour les entites recurrentes
    extra = []
    if m >= 2:
        budget = 4 if m >= 5 else 1
        for q in early_modern_variants(label) + cl.get("variants", [])[:3]:
            if norm(q) and norm(q) not in {norm(x) for x in forms + extra}:
                extra.append(q)
            if len(extra) >= budget:
                break

    # ETAPES de recherche par ordre de cout/pertinence, bornees selon la frequence
    # (on ne paie pas 8 appels pour un cluster vu 2 fois). Sortie anticipee des
    # qu'un match confiant est trouve -> entites notables = 2 appels, pas 24.
    stages = [(forms[0], "fr")]
    if len(forms) > 1:
        stages.append((forms[1], "fr"))
    stages.append((forms[0], "la"))
    if m >= 5:
        # traitement complet pour les entites recurrentes (meilleur rappel)
        if len(forms) > 1:
            stages.append((forms[1], "la"))
        for q in extra:
            stages.append((q, "fr")); stages.append((q, "la"))
        stages.append((forms[0], "en"))   # dernier recours

    cand = []
    best = None  # ((score), qid, e, fame)
    for (q, lang) in stages:
        ids = wbsearch(q, lang)
        new = [qid for qid in ids if qid not in cand]
        if not new:
            continue
        cand.extend(new)
        ents = wbget(new)
        for qid in new:
            e = ents.get(qid)
            if not e:
                continue
            sc = score_candidate(typ, nlabel, qid, e, cand.index(qid), accept)
            if sc is None:
                continue
            tup, fame = sc
            if best is None or tup > best[0]:
                best = (tup, qid, e, fame)
        # sortie anticipee : match exact + notoriete/autorite suffisante
        if best is not None:
            (sim, sf, _), bqid, be, bfame = best
            has_auth = any(get_str(be, p) for p in ("P213", "P214", "P227", "P268", "P1566"))
            if (sim == 3 and (bfame >= 6 or has_auth)) or (sim >= 2 and bfame >= 25):
                break

    if best is None:
        return None
    (sim, sf, _), qid, e, fame = best
    has_auth = any(get_str(e, p) for p in ("P213", "P214", "P227", "P268", "P1566"))
    if sim == 3 and (fame >= 6 or has_auth):
        conf = "high"
    elif sim >= 2 and (fame >= 3 or has_auth):
        conf = "medium"
    else:
        conf = "low"
    # materiaux / techniques / objets : termes abstraits tres ambigus (ex: "matiere"
    # -> discipline academique, "terre" -> planete Terre). On n'asserte un match que
    # s'il porte un identifiant Getty AAT (thesaurus des materiaux/techniques) ;
    # sinon il reste une simple piste (low).
    if typ in ("material", "technique", "artwork") and not get_str(e, "P1014"):
        conf = "low"

    rec = build_record(typ, qid, e)
    rec["match_confidence"] = conf
    rec["wikidata_sitelinks"] = fame
    alts = [q for q in cand if q != qid][:3]
    if alts:
        rec["wikidata_alts"] = alts
    return rec

def build_record(typ, qid, e):
    rec = {
        "wikidata_qid": qid,
        "wikidata_label": label_of(e),
        "description": desc_of(e),
    }
    # identifiants externes communs
    for pid, key in [("P213", "isni"), ("P214", "viaf"), ("P227", "gnd"),
                     ("P268", "bnf"), ("P244", "lccn"), ("P1014", "aat"),
                     ("P1566", "geonames"), ("P18", "image"), ("P373", "commons")]:
        v = get_str(e, pid)
        if v:
            rec[key] = v
    if typ == "person":
        rec["birth"] = fmt_time(get_time(e, "P569"))
        rec["death"] = fmt_time(get_time(e, "P570"))
        rec["occupation_qids"] = get_qid_claims(e, "P106")[:4]
        rec["gender_qid"] = get_qid_claim(e, "P21")
        rec["citizenship_qids"] = get_qid_claims(e, "P27")[:2]
    elif typ == "place":
        c = get_coord(e)
        if c:
            rec["lat"], rec["lon"] = c
        rec["country_qid"] = get_qid_claim(e, "P17")
        rec["admin_qid"] = get_qid_claim(e, "P131")
        rec["place_type_qids"] = get_qid_claims(e, "P31")[:3]
    elif typ == "org":
        rec["inception"] = fmt_time(get_time(e, "P571"))
        rec["org_type_qids"] = get_qid_claims(e, "P31")[:3]
        rec["hq_qid"] = get_qid_claim(e, "P159")
    elif typ == "work":
        rec["author_qids"] = get_qid_claims(e, "P50")[:3]
        rec["pub_date"] = fmt_time(get_time(e, "P577"))
        rec["genre_qids"] = get_qid_claims(e, "P136")[:3]
        rec["lang_qid"] = get_qid_claim(e, "P407")
    elif typ in ("material", "technique", "artwork"):
        rec["class_qids"] = get_qid_claims(e, "P31")[:3]
    elif typ == "event":
        rec["when"] = fmt_time(get_time(e, "P585"))
        rec["start"] = fmt_time(get_time(e, "P580"))
        rec["end"] = fmt_time(get_time(e, "P582"))
        rec["location_qid"] = get_qid_claim(e, "P276")
    return {k: v for k, v in rec.items() if v not in (None, [], "")}

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-mentions", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--types", default="")
    args = ap.parse_args()

    clusters = json.load(open(CLUSTERS, encoding="utf-8"))
    types = set(t.strip() for t in args.types.split(",") if t.strip())
    work = [c for c in clusters
            if c["mentions"] >= args.min_mentions
            and (not types or c["type"] in types)
            and c["type"] != "date"]
    work.sort(key=lambda c: -c["mentions"])
    if args.limit:
        work = work[:args.limit]

    load_cache()
    # reprise : recharge resultats deja calcules
    done = {}
    if os.path.exists(OUT):
        try:
            for r in json.load(open(OUT, encoding="utf-8")):
                done[(r["type"], r["label_norm"])] = r
        except Exception:
            done = {}

    pending = [cl for cl in work if (cl["type"], cl["label_norm"]) not in done]
    print(f"Clusters a reconcilier : {len(work)} | deja faits : {len(work)-len(pending)} "
          f"| restants : {len(pending)} (min_mentions={args.min_mentions})", flush=True)

    results = list(done.values())
    matched = sum(1 for r in results if r.get("wikidata_qid"))
    rlock = threading.Lock()
    t0 = time.time()
    counter = {"n": 0}

    def worker(cl):
        try:
            return cl, reconcile_one(cl)
        except Exception as ex:
            print(f"  ! err {cl['type']}/{cl['label']!r}: {ex}", file=sys.stderr, flush=True)
            return cl, None

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(worker, cl) for cl in pending]
        for fut in as_completed(futs):
            cl, wd = fut.result()
            rec = dict(cl)
            with rlock:
                if wd:
                    rec.update(wd)
                    matched += 1
                results.append(rec)
                counter["n"] += 1
                n = counter["n"]
                if n % 50 == 0:
                    json.dump(results, open(OUT, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    rate = n / max(1e-6, time.time() - t0)
                    eta = (len(pending) - n) / max(1e-6, rate) / 60
                    print(f"  {n}/{len(pending)} | {matched} matchs WD "
                          f"| {rate:.1f}/s | ETA {eta:.0f} min", flush=True)
                if n % 400 == 0:
                    save_cache()

    save_cache()
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nTermine. {counter['n']} nouveaux traites, "
          f"{matched}/{len(results)} avec QID Wikidata.")
    print(f"Sortie : {OUT}")

if __name__ == "__main__":
    main()
