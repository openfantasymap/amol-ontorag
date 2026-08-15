#!/usr/bin/env python3
"""
compose.py — the OntoRAG composition model.

Treat each sourcebook as a semantic PACK (the entities + chunks + embeddings
attributed to it) and compose "core + a subset of packs" into a scoped world.

A pack = one book (content/books.json), linked by a `dc:requires` dependency graph
(every supplement requires the core rules; core requires nothing). Selecting packs
and closing over `requires` yields a scoped sub-dataset:

    chunk / vector in scope  <=>  its book is in the selected closure
    entity in scope          <=>  attestedIn ∩ closure ≠ ∅
                                  OR it is a SPINE entity (definedIn empty — the
                                  shared, always-present core vocabulary)

Add a pack → its world appears; remove it → gone (along with anything that
requires it). Because chunks/vectors are stored one file per book, a composed view
is literally a subset of the dataset's files — which is also the seam along which
packs could later become separate repositories.

Usage:
  compose.py --validate                          # integrity + soundness of the whole dataset
  compose.py --packs covenants                   # a scoped view (counts + files)
  compose.py --packs covenants,mystery-cults     # multiple packs
  compose.py --packs covenants --json view.json  # write the composed view manifest
  compose.py                                     # the full composition (all packs)
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(dataset):
    books = json.load(open(os.path.join(dataset, "content/books.json"), encoding="utf-8"))
    ents = [json.loads(l) for l in open(os.path.join(dataset, "ontology/entities.jsonl"), encoding="utf-8")]
    try:
        sources = json.load(open(os.path.join(dataset, "content/sources.json"), encoding="utf-8"))
    except FileNotFoundError:
        sources = {}
    return books, ents, sources


def closure(packs, books):
    """Close a set of pack ids over dc:requires."""
    out, stack = set(), list(packs)
    while stack:
        p = stack.pop()
        if p in out:
            continue
        out.add(p)
        stack.extend(books.get(p, {}).get("requires", []))
    return out


def roots(books):
    return sorted(p for p, m in books.items() if not m.get("requires"))


def resolve(tokens, books):
    """Resolve user pack tokens to slugs (exact, else unique substring)."""
    out = set()
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok in books:
            out.add(tok)
            continue
        cands = [p for p in books if tok in p]
        if len(cands) == 1:
            out.add(cands[0])
        elif not cands:
            raise SystemExit("no pack matches %r" % tok)
        else:
            raise SystemExit("ambiguous pack %r -> %s" % (tok, cands))
    return out


def compose(tokens, books, ents, sources):
    selected = resolve(tokens, books) if tokens else set(books)
    scope = closure(selected, books)
    spine = [e for e in ents if not e.get("definedIn")]
    book_ents = [e for e in ents if e.get("definedIn")]
    in_scope = [e for e in book_ents if set(e.get("attestedIn", [])) & scope]
    n_chunks = sum(sources.get(p, {}).get("chunks", 0) for p in scope)
    return {
        "selected": sorted(selected),
        "closure": sorted(scope),
        "added_by_requires": sorted(scope - selected),
        "chunk_files": ["content/chunks/%s.jsonl" % p for p in sorted(scope)],
        "vector_files": ["embeddings/vectors/%s.jsonl" % p for p in sorted(scope)],
        "counts": {
            "packs": len(scope),
            "chunks": n_chunks,
            "entities_in_scope": len(spine) + len(in_scope),
            "spine_entities": len(spine),
            "book_entities_in_scope": len(in_scope),
            "book_entities_total": len(book_ents),
        },
    }


def validate(books, ents, sources, dataset):
    errs, warns = [], []

    for p, m in books.items():
        for r in m.get("requires", []):
            if r not in books:
                errs.append("pack %s requires unknown pack %s" % (p, r))

    color = {p: 0 for p in books}  # 0 white, 1 gray, 2 black

    def dfs(p):
        color[p] = 1
        for r in books.get(p, {}).get("requires", []):
            if color.get(r) == 1:
                errs.append("cycle in dc:requires at %s -> %s" % (p, r))
            elif color.get(r) == 0:
                dfs(r)
        color[p] = 2
    for p in books:
        if color[p] == 0:
            dfs(p)

    for e in ents:
        for slot in ("attestedIn", "definedIn"):
            for s in e.get(slot, []):
                if s not in books:
                    errs.append("entity %s %s -> unknown pack %s" % (e["iri"], slot, s))

    ent_iris = {e["iri"] for e in ents}
    dangling = 0
    for f in glob.glob(os.path.join(dataset, "content/chunks/*.jsonl")):
        for line in open(f, encoding="utf-8"):
            for iri in json.loads(line).get("entities", []):
                if iri not in ent_iris:
                    dangling += 1
    if dangling:
        errs.append("%d dangling chunk->entity references" % dangling)

    for p in books:
        if not os.path.exists(os.path.join(dataset, "content/chunks/%s.jsonl" % p)):
            warns.append("pack %s has no chunk file" % p)

    md = sum(1 for e in ents if e.get("definedIn")
             and not (set(e.get("definedIn", [])) & set(e.get("attestedIn", []))))
    if md:
        warns.append("%d entities defined in a pack that does not attest them" % md)
    return errs, warns


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=ROOT)
    ap.add_argument("--packs", default="", help="comma-separated pack ids/substrings; empty = all")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--json", help="write the composed view manifest to this path")
    args = ap.parse_args()

    books, ents, sources = load(args.dataset)

    if args.validate:
        errs, warns = validate(books, ents, sources, args.dataset)
        print("packs: %d | core (roots): %s" % (len(books), roots(books)))
        for w in warns:
            print("  WARN:", w)
        for e in errs:
            print("  ERROR:", e)
        print("VALID" if not errs else "INVALID (%d errors)" % len(errs))
        sys.exit(0 if not errs else 1)

    tokens = args.packs.split(",") if args.packs else []
    view = compose(tokens, books, ents, sources)
    print(json.dumps(view["counts"], indent=2))
    print("closure (%d packs):" % len(view["closure"]), view["closure"])
    if view["added_by_requires"]:
        print("added by dc:requires:", view["added_by_requires"])
    if args.json:
        json.dump(view, open(args.json, "w"), indent=2)
        print("wrote", args.json)


if __name__ == "__main__":
    main()
