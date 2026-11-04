#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Registre d'identifiants stables (id_registry.csv).

Un id de registre (`person-000042`) est attribue une fois pour toutes a une
entite et n'est jamais reutilise, meme apres suppression ou fusion. Recherche :
  1. cle exacte (type, label_norm) ;
  2. sinon chevauchement maximal des UUID NER (meme type) -> l'id suit
     l'entite quand son libelle normalise change ;
  3. sinon nouvel id <prefixe><max+1 sur 6 chiffres>.

CSV : type,label_norm,xml_id,status,ner_ids,created,updated
      status in active|merged|deleted ; ner_ids joints par "|" ; dates ISO.

stdlib uniquement.
"""
import csv
import os
from datetime import date

PREFIX = {
    "person": "person-", "place": "place-", "org": "org-", "work": "work-",
    "event": "event-", "artwork": "artwork-", "material": "material-",
    "technique": "technique-", "date": "date-",
}
FIELDS = ["type", "label_norm", "xml_id", "status", "ner_ids", "created", "updated"]


class IdRegistry:
    def __init__(self, path, today=None):
        self.path = path
        self.today = today or date.today().isoformat()
        self._rows = {}      # xml_id -> row (ner_ids as set)
        self._by_key = {}    # (type, label_norm) -> xml_id
        self._claimed = set()  # ids deja attribues pendant cette session

    def load(self):
        self._rows, self._by_key, self._claimed = {}, {}, set()
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                xid = (r.get("xml_id") or "").strip()
                if not xid:
                    continue
                row = {k: (r.get(k) or "").strip() for k in FIELDS}
                row["ner_ids"] = {u for u in row["ner_ids"].split("|") if u}
                self._rows[xid] = row
                self._by_key[(row["type"], row["label_norm"])] = xid

    def save(self):
        tmp = self.path + ".tmp"
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for xid in sorted(self._rows):
                row = dict(self._rows[xid])
                row["ner_ids"] = "|".join(sorted(row["ner_ids"]))
                w.writerow(row)
        os.replace(tmp, self.path)

    def get(self, typ, label_norm):
        return self._by_key.get((typ, label_norm))

    def ids(self):
        return set(self._rows)

    def mark(self, xml_id, status):
        row = self._rows.get(xml_id)
        if row is not None and row["status"] != status:
            row["status"] = status
            row["updated"] = self.today

    def _touch(self, row, ner_ids):
        new = set(ner_ids) - row["ner_ids"]
        if new or row["status"] != "active":
            row["ner_ids"] |= new
            row["status"] = "active"
            row["updated"] = self.today
        self._claimed.add(row["xml_id"])
        return row["xml_id"]

    def assign(self, typ, label_norm, ner_ids):
        if typ not in PREFIX:
            raise ValueError("type d'entite inconnu : %r" % typ)
        xid = self._by_key.get((typ, label_norm))
        if xid is not None:
            return self._touch(self._rows[xid], ner_ids)
        # chevauchement d'UUID NER (meme type, id pas encore pris cette session)
        want = set(ner_ids)
        best, best_n = None, 0
        for row in self._rows.values():
            if row["type"] != typ or row["xml_id"] in self._claimed:
                continue
            n = len(want & row["ner_ids"])
            if n > best_n or (n == best_n and n and row["xml_id"] < best["xml_id"]):
                best, best_n = row, n
        if best is not None:
            del self._by_key[(typ, best["label_norm"])]
            best["label_norm"] = label_norm
            best["updated"] = self.today
            self._by_key[(typ, label_norm)] = best["xml_id"]
            return self._touch(best, ner_ids)
        # nouvel id : jamais reutilise -> max de tous les ids existants + 1
        pre = PREFIX[typ]
        nums = [int(x[len(pre):]) for x in self._rows
                if x.startswith(pre) and x[len(pre):].isdigit()]
        xid = "%s%06d" % (pre, max(nums, default=0) + 1)
        self._rows[xid] = {"type": typ, "label_norm": label_norm, "xml_id": xid,
                           "status": "active", "ner_ids": want,
                           "created": self.today, "updated": self.today}
        self._by_key[(typ, label_norm)] = xid
        self._claimed.add(xid)
        return xid
