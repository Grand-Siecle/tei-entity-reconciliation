#!/usr/bin/env python3
"""Etape 4 du pipeline NER -> reecrit les @ref des documents TEI :
UUID NER (#pers-ab12...) -> id de registre (#person-000432), via id_mapping.csv.

POURQUOI on ne touche QUE l'attribut @ref (jamais les xml:id du standOff) :
  le mapping est PLUSIEURS-VERS-UN (la deduplication a fusionne plusieurs UUID
  vers un meme id de registre). Reecrire les xml:id du standOff creerait donc des
  xml:id en double dans un meme document -> XML invalide / erreur d'indexation eXist.
  Les @ref, eux, doivent pointer vers le registre global : c'est exactement le but.

FORMAT cible : on CONSERVE le '#' de tete.
  L'ODD (resources/odd/grand_siecle.odd) fait `replace(@ref, '^#', '')` pour la cle
  de lookup, ET surligne les oeuvres seulement si `starts-with(@ref, '#work-')`.
  Donc '#person-000432' est la forme correcte et sure pour tous les types.

Les refs absentes du mapping (entites non reconciliees) sont laissees TELLES QUELLES
  -> elles continuent de pointer vers le standOff local du document (toujours valide).

Streaming ligne par ligne -> memoire quasi constante, OK pour des fichiers >200 Mo
(un attribut @ref ne traverse jamais un saut de ligne).

Usage :
  rewrite_refs.py FICHIER...                # reecriture en place (atomique)
  rewrite_refs.py --dry-run FICHIER...      # compte seulement, n'ecrit rien
  rewrite_refs.py --out DIR FICHIER...      # ecrit dans DIR, originaux intacts (preview)
  rewrite_refs.py --mapping CHEMIN ...      # mapping alternatif
                                            # (defaut: ../data/registers/id_mapping.csv)

Sortie : une ligne 'COUNT <n> <fichier>' par fichier, puis 'TOTAL <n>'.
"""
import argparse, csv, os, re, tempfile

# n'attrape QUE l'attribut ref (precede d'un espace) -> jamais xml:id, resp, corresp...
REF = re.compile(r'(\sref=")([^"]*)(")')


def load_mapping(path):
    m = {}
    with open(path, newline='', encoding='utf-8') as f:
        rd = csv.reader(f)
        next(rd, None)  # entete : ner_uuid,register_id,type
        for row in rd:
            if len(row) >= 2 and row[0]:
                m[row[0]] = row[1]
    return m


def make_repl(mapping, counter):
    def repl(mt):
        pre, val, post = mt.groups()
        out = []
        for tok in val.split():            # gere les @ref multi-valeurs
            if tok.startswith('#') and tok[1:] in mapping:
                out.append('#' + mapping[tok[1:]])
                counter[0] += 1
            else:
                out.append(tok)
        return pre + ' '.join(out) + post
    return repl


def process(path, mapping, out_dir=None, dry=False):
    counter = [0]
    repl = make_repl(mapping, counter)

    if dry:
        with open(path, encoding='utf-8') as f:
            for line in f:
                REF.sub(repl, line)
        return counter[0]

    if out_dir:
        dst = os.path.join(out_dir, os.path.basename(path))
        with open(path, encoding='utf-8') as fin, open(dst, 'w', encoding='utf-8') as fo:
            for line in fin:
                fo.write(REF.sub(repl, line))
        return counter[0]

    # en place, atomique (temp dans le meme repertoire puis os.replace)
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    with open(path, encoding='utf-8') as fin, os.fdopen(fd, 'w', encoding='utf-8') as fo:
        for line in fin:
            fo.write(REF.sub(repl, line))
    if counter[0] > 0:
        os.replace(tmp, path)
    else:
        os.remove(tmp)  # rien a changer : on ne reecrit pas le fichier
    return counter[0]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_map = os.path.normpath(
        os.path.join(here, '..', 'data', 'registers', 'id_mapping.csv'))

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+', help='fichiers TEI a traiter')
    ap.add_argument('--mapping', default=default_map,
                    help='id_mapping.csv (defaut: data/registers/id_mapping.csv)')
    ap.add_argument('--out', help='ecrire dans ce repertoire au lieu de modifier en place')
    ap.add_argument('--dry-run', action='store_true', help='compter sans rien ecrire')
    a = ap.parse_args()

    mapping = load_mapping(a.mapping)
    if a.out and not a.dry_run:
        os.makedirs(a.out, exist_ok=True)

    total = 0
    for p in a.files:
        n = process(p, mapping, a.out, a.dry_run)
        total += n
        print(f"COUNT {n} {os.path.basename(p)}")
    print(f"TOTAL {total}")


if __name__ == '__main__':
    main()
