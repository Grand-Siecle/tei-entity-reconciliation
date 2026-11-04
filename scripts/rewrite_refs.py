#!/usr/bin/env python3
"""Etape 4 du pipeline NER -> reecrit les @ref des documents TEI :
UUID NER (#pers-ab12...) -> id de registre (#person-000432), via id_mapping.csv,
ET reconstruit les listes internes du document (standOff, teiHeader) avec ces ids.

POURQUOI on reconstruit aussi les listes internes (issue #1) :
  apres une reecriture des seuls @ref, le corps pointait vers le registre
  (#person-000432) alors que les listes du standOff / particDesc gardaient les
  xml:id NER (pers-<uuid>). Les jointures entete <-> corps par id ne trouvaient
  donc plus rien, et tout consommateur qui construit la liste des entites d'un
  document a partir de listPerson/person/@xml:id obtenait zero mention.
  Correctif retenu (option 1 de l'issue) : dans les blocs <teiHeader> et
  <standOff>, tout element dont le xml:id est un UUID du mapping prend l'id de
  registre, UNE seule entree par id de registre.

  Le mapping est PLUSIEURS-VERS-UN (la deduplication a fusionne plusieurs UUID
  vers un meme id) : renommer betement creerait des xml:id en double -> XML
  invalide / erreur d'indexation eXist. On FUSIONNE donc les entrees :
    - la premiere entree (ordre du document) est gardee et renommee ;
    - les noms des autres entrees dont le texte differe sont ajoutes a la
      premiere avec type="variant" ;
    - le @cert le plus fort est conserve (high > medium > low > absent) ;
    - les autres entrees sont supprimees.
  Les jetons de @target / @corresp (et @ref) qui sont des UUID du mapping sont
  reecrits aussi (les linkGrp/link restent valides) ; les ids de mention
  (pers-<uuid>-m1) et de segments (ent-xxxx-0) ne sont JAMAIS des cles du
  mapping, ils ne bougent donc pas. Les entrees non reconciliees restent telles
  quelles.

FORMAT cible : on CONSERVE le '#' de tete.
  L'ODD (resources/odd/grand_siecle.odd) fait `replace(@ref, '^#', '')` pour la cle
  de lookup, ET surligne les oeuvres seulement si `starts-with(@ref, '#work-')`.
  Donc '#person-000432' est la forme correcte et sure pour tous les types.

Dans le <text>, on ne touche QUE l'attribut @ref. Les refs absentes du mapping
  (entites non reconciliees) sont laissees TELLES QUELLES -> elles continuent de
  pointer vers le standOff local du document (toujours valide).

Streaming ligne par ligne pour le corps -> memoire quasi constante, OK pour des
fichiers >200 Mo (un attribut @ref ne traverse jamais un saut de ligne). Seuls
les blocs <teiHeader> et <standOff> (petits) sont parses en entier (xml.etree).
Fichier minifie (bloc ouvert en milieu de ligne) -> repli sur un traitement du
texte entier. Un fichier sans rien a changer reste identique a l'octet pres, et
un second passage ne change rien (COUNT 0).

Usage :
  rewrite_refs.py FICHIER...                # reecriture en place (atomique)
  rewrite_refs.py --dry-run FICHIER...      # compte seulement, n'ecrit rien
  rewrite_refs.py --out DIR FICHIER...      # ecrit dans DIR, originaux intacts (preview)
  rewrite_refs.py --mapping CHEMIN ...      # mapping alternatif
                                            # (defaut: ../data/registers/id_mapping.csv)
  rewrite_refs.py --no-standoff ...         # ancien comportement : @ref seulement,
                                            # listes internes intactes

Sortie : une ligne 'COUNT <n> <fichier>' par fichier, puis 'TOTAL <n>'
(n = refs reecrites + modifications dans les listes internes).
"""
import argparse, copy, csv, os, re, sys, tempfile
import xml.etree.ElementTree as ET

# n'attrape QUE l'attribut ref (precede d'un espace) -> jamais xml:id, resp, corresp...
REF = re.compile(r'(\sref=")([^"]*)(")')

TEI_NS = 'http://www.tei-c.org/ns/1.0'
XMLID = '{http://www.w3.org/XML/1998/namespace}id'
# balise ouvrante d'un bloc a reconstruire (auto-fermante reperee par '/>')
OPEN = re.compile(r'<(teiHeader|standOff)(?=[\s/>])[^>]*>')
# bloc complet, pour le repli texte entier (pas les balises auto-fermantes)
BLOCK = re.compile(r'<(teiHeader|standOff)(?=[\s>])[^>]*(?<!/)>.*?</\1>', re.S)
NAME_TAGS = {'persName', 'placeName', 'orgName', 'label', 'title', 'objectName', 'name'}
POINTER_ATTRS = ('ref', 'target', 'corresp')
CERT_RANK = {'high': 3, 'medium': 2, 'low': 1}

ET.register_namespace('', TEI_NS)  # bloc qui declare xmlns TEI -> pas de 'ns0:'


class _Fallback(Exception):
    """Bloc ouvert en milieu de ligne / sur une seule ligne -> texte entier."""


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


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _text_of(el):
    return ' '.join(''.join(el.itertext()).split())


def transform_block(xml_text, mapping):
    """Reconstruit un bloc <teiHeader>/<standOff> avec les ids de registre.

    Renvoie (texte_xml, nb_modifications). Leve ET.ParseError si le bloc
    n'est pas du XML bien forme isolable (entite non declaree, etc.).
    """
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    parser.feed(xml_text)
    root = parser.close()
    parent = {c: p for p in root.iter() for c in p}
    targets = set(mapping.values())
    n = 0

    # 1) pointeurs : jetons '#uuid' du mapping dans @ref / @target / @corresp
    for el in root.iter():
        for att in POINTER_ATTRS:
            val = el.get(att)
            if not val:
                continue
            toks = val.split()
            new = ['#' + mapping[t[1:]] if t.startswith('#') and t[1:] in mapping else t
                   for t in toks]
            if new != toks:
                n += sum(1 for a, b in zip(toks, new) if a != b)
                el.set(att, ' '.join(new))

    # 2) xml:id : renommage + fusion des entrees mappees vers le meme id
    kept = {}        # id de registre -> element garde
    removed = set()
    for el in list(root.iter()):
        if el not in parent or not isinstance(el.tag, str):
            continue
        xid = el.get(XMLID)
        if xid in mapping:
            tgt = mapping[xid]
        elif xid in targets:
            tgt = xid           # deja reecrit (passage precedent)
        else:
            continue
        p, gone = el, False
        while p in parent:      # descendant d'une entree deja supprimee ?
            p = parent[p]
            if p in removed:
                gone = True
                break
        if gone:
            continue
        if tgt not in kept:
            kept[tgt] = el
            if xid != tgt:
                el.set(XMLID, tgt)
                n += 1
            continue
        _merge_into(kept[tgt], el)
        par = parent[el]
        siblings = list(par)
        i = siblings.index(el)
        if i > 0:
            siblings[i - 1].tail = el.tail  # garde l'indentation de fermeture
        par.remove(el)
        removed.add(el)
        n += 1

    if n == 0:
        return xml_text, 0
    return ET.tostring(root, encoding='unicode'), n


def _merge_into(keep, other):
    names = {_text_of(c) for c in keep if _local(c.tag) in NAME_TAGS}
    for c in other:
        if _local(c.tag) not in NAME_TAGS:
            continue
        t = _text_of(c)
        if t in names:
            continue
        names.add(t)
        v = copy.deepcopy(c)
        v.set('type', 'variant')
        keep.append(v)
    if CERT_RANK.get(other.get('cert'), 0) > CERT_RANK.get(keep.get('cert'), 0):
        keep.set('cert', other.get('cert'))


def _apply_block(block, mapping, counter, repl, name):
    try:
        new, n = transform_block(block, mapping)
    except ET.ParseError as e:
        print('WARN %s: bloc non parsable (%s), @ref seulement' % (name, e), file=sys.stderr)
        return REF.sub(repl, block)
    counter[0] += n
    return new


def rewrite_document(text, mapping, counter, name='?'):
    """Repli texte entier : blocs teiHeader/standOff reconstruits, @ref ailleurs."""
    repl = make_repl(mapping, counter)
    out, pos = [], 0
    for m in BLOCK.finditer(text):
        out.append(REF.sub(repl, text[pos:m.start()]))
        out.append(_apply_block(m.group(0), mapping, counter, repl, name))
        pos = m.end()
    out.append(REF.sub(repl, text[pos:]))
    return ''.join(out)


def _opener(line):
    """Balise ouvrante de bloc dans la ligne -> (match, fermeture) ou None.
    Leve _Fallback si le bloc ne commence pas la ligne ou s'y ferme."""
    if '<standOff' not in line and '<teiHeader' not in line:
        return None
    m = OPEN.search(line)
    if not m:
        return None
    if line[:m.start()].strip():
        raise _Fallback()
    if m.group(0).endswith('/>'):
        if OPEN.search(line, m.end()):
            raise _Fallback()
        return None
    close = '</%s>' % m.group(1)
    if close in line:
        raise _Fallback()
    return close


def _stream(fin, write, mapping, counter, name):
    repl = make_repl(mapping, counter)
    buf, close = None, None
    for line in fin:
        if buf is not None:
            buf.append(line)
            if close not in line:
                continue
            text = ''.join(buf)
            buf = None
            end = text.index(close) + len(close)
            block, rest = text[:end], text[end:]
            core = block.lstrip()
            write(block[:len(block) - len(core)])
            write(_apply_block(core, mapping, counter, repl, name))
            if '<standOff' in rest or '<teiHeader' in rest:
                raise _Fallback()
            write(REF.sub(repl, rest))
            continue
        close = _opener(line)
        if close:
            buf = [line]
            continue
        write(REF.sub(repl, line))
    if buf is not None:
        print('WARN %s: %s introuvable, @ref seulement' % (name, close), file=sys.stderr)
        write(REF.sub(repl, ''.join(buf)))


def _transform_file(path, mapping, fo, standoff):
    """Ecrit la version reecrite de path dans fo ; renvoie le nombre de changements."""
    name = os.path.basename(path)
    counter = [0]
    if not standoff:  # ancien comportement : @ref seulement
        repl = make_repl(mapping, counter)
        with open(path, encoding='utf-8') as fin:
            for line in fin:
                fo.write(REF.sub(repl, line))
        return counter[0]
    try:
        with open(path, encoding='utf-8') as fin:
            _stream(fin, fo.write, mapping, counter, name)
    except _Fallback:
        counter[0] = 0
        fo.seek(0)
        fo.truncate()
        with open(path, encoding='utf-8') as fin:
            fo.write(rewrite_document(fin.read(), mapping, counter, name))
    return counter[0]


class _Null:
    def write(self, s):
        pass

    def seek(self, pos):
        pass

    def truncate(self):
        pass


def process(path, mapping, out_dir=None, dry=False, standoff=True):
    if dry:
        return _transform_file(path, mapping, _Null(), standoff)

    # temp dans le repertoire cible puis os.replace (atomique)
    d = out_dir or os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fo:
            n = _transform_file(path, mapping, fo, standoff)
    except BaseException:
        os.remove(tmp)
        raise
    if out_dir:
        os.replace(tmp, os.path.join(out_dir, os.path.basename(path)))
    elif n > 0:
        os.replace(tmp, path)
    else:
        os.remove(tmp)  # rien a changer : on ne reecrit pas le fichier
    return n


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
    ap.add_argument('--no-standoff', action='store_true',
                    help='ne reecrire que les @ref (listes standOff/teiHeader intactes)')
    a = ap.parse_args()

    mapping = load_mapping(a.mapping)
    if a.out and not a.dry_run:
        os.makedirs(a.out, exist_ok=True)

    total = 0
    for p in a.files:
        n = process(p, mapping, a.out, a.dry_run, not a.no_standoff)
        total += n
        print(f"COUNT {n} {os.path.basename(p)}")
    print(f"TOTAL {total}")


if __name__ == '__main__':
    main()
