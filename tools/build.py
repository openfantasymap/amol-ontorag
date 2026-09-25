#!/usr/bin/env python3
"""
build.py — turn the Ars Magica Open License markdown corpus into an OntoRAG
GitHub-as-storage dataset: entity index + content chunks + embeddings + manifest.

Pipeline:
    world.ttl ──(rdflib)──> ontology/entities.jsonl   (flat index + alias dictionary)
    corpus/*.md ──────────> content/chunks/<slug>.jsonl   (heading+window chunking,
                                                            entity-linked)
    chunks ──(provider)───> embeddings/vectors/<slug>.jsonl
    everything ───────────> content/sources.json, embeddings/config.json, manifest.json

Dependencies: rdflib (ontology parse). Embedding providers:
  * ollama  (default) — local server, real semantic vectors, no API key.
  * hashed            — pure stdlib, deterministic, zero-dependency fallback.
Both keep the build runnable offline; see embeddings/README.md to swap in
voyage / openai / sentence-transformers.

Designed to run on a modern Python via Docker (see tools/Dockerfile,
docker-compose.yml) but degrades to host Python 3.8 with the `hashed` provider.
"""
import argparse
import datetime as _dt
import glob
import hashlib
import json
import math
import os
import re
import sys
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Documents included in the sample build (relative to the corpus root).
# These two books between them mention 8 of the 12 Houses, so entity linking
# and cross-document retrieval are both exercised.
DEFAULT_DOCS = [
    "reviewed/Ars Magica 5e - Houses of Hermes - True Lineages.md",
    "reviewed/Ars Magica 5e - Houses of Hermes - Mystery Cults.md",
]
CORPUS_REPO = "https://github.com/OriginalMadman/Ars-Magica-Open-License"
LICENSE = "CC-BY-SA-4.0"
# Canonical OntoRAG dataset format schemas (https://ontorag.org/vocab/#format)
SPEC = "https://ontorag.org/vocab/dataset/0.1/"

# ---------------------------------------------------------------------------
# Slugs & text cleaning
# ---------------------------------------------------------------------------

def slugify(name):
    name = re.sub(r"\.md$", "", name)
    name = re.sub(r"^Ars Magica\s*\d*e?\s*-\s*", "", name)  # drop "Ars Magica 5e - "
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return name

_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")           # [label](url) -> label
_ENT_RE = re.compile(r"&[a-z]+;")                          # &emsp; &nbsp; ...

def clean_text(s):
    s = _LINK_RE.sub(r"\1", s)
    s = _TAG_RE.sub(" ", s)
    s = _ENT_RE.sub(" ", s)
    s = s.replace(" ", " ")
    s = re.sub(r"^\s*>\s?", "", s)                         # blockquote marker
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def clean_heading(s):
    return clean_text(re.sub(r"^#{1,6}\s+", "", s)).strip()

# ---------------------------------------------------------------------------
# Provenance helpers (book attestation)
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")

def norm_name(s):
    """Normalize an entity name for matching against the extraction shard
    (mirrors tools/ttl_from_entities.py:norm)."""
    s = re.sub(r"^the\s+", "", s.lower())
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return _WS.sub(" ", s).strip()

def load_evidence(path):
    """Map normalized entity name -> set of doc slugs the entity was EXTRACTED
    from, read from the extraction shard's `evidence` chunk ids (id = '<doc>::NNNN').
    This is the 'defining source' provenance; missing/curated entities map to {}."""
    out = defaultdict(set)
    if not os.path.exists(path):
        return out
    data = json.load(open(path, encoding="utf-8"))
    recs = data if isinstance(data, list) else data.get("entities", [])
    for e in recs:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        docs = {cid.split("::", 1)[0] for cid in (e.get("evidence") or []) if "::" in cid}
        if docs:
            out[norm_name(name)].update(docs)
    return out

# ---------------------------------------------------------------------------
# Ontology -> entity index + alias dictionary
# ---------------------------------------------------------------------------

def load_entities(ttl_path):
    """Parse world.ttl and project it to flat entity records (entities.jsonl)."""
    from rdflib import Graph, RDF, RDFS, Namespace
    from rdflib.namespace import SKOS, DCTERMS

    g = Graph()
    g.parse(ttl_path, format="turtle")
    AMOL = Namespace("https://www.fantasymaps.org/amol-ontorag/id/")
    RPG = Namespace("http://www.rpg-schema.org/1.0/")
    SCHEMA = Namespace("https://schema.org/")

    def lits(s, p):
        return [str(o) for o in g.objects(s, p)]

    # Classifier/grouping nodes (objects of rpg:hasTag / rpg:ruleSetType) are part
    # of the graph but are meta-categories, not entities to be matched in prose.
    categories = set()
    for _, _, o in g.triples((None, RPG.hasTag, None)):
        categories.add(str(o))
    for _, _, o in g.triples((None, RPG.ruleSetType, None)):
        categories.add(str(o))

    # orp: sources, files and packs (the books, their files and their packs, emitted
    # by tools/provenance.py) are provenance metadata, not world entities: keep them
    # out of the index and out of the alias matcher (else "Covenants" the book would
    # collide with "Covenant" the concept).
    ORP = Namespace("https://ontorag.org/provenance#")
    book_iris = {str(s) for t in (ORP.Source, ORP.SourceFile, ORP.Pack, ORP.SpinePack)
                 for s in g.subjects(RDF.type, t)}

    entities = []
    for s in set(g.subjects()):
        if not str(s).startswith(str(AMOL)):
            continue
        if str(s) in book_iris:
            continue
        types = [str(t) for t in g.objects(s, RDF.type)]
        if not types:
            continue
        # Skip vocabulary terms (rdfs:Class / rdf:Property) such as the provenance
        # vocabulary emitted by tools/provenance.py — schema, not world entities.
        if {str(RDF.Property), str(RDFS.Class)} & set(types):
            continue
        labels = lits(s, RPG.name) or lits(s, SCHEMA.name) or lits(s, RDFS.label) or lits(s, SKOS.prefLabel)
        if not labels:
            continue
        label = labels[0]
        aliases = set(labels)
        for p in (SKOS.altLabel, SKOS.prefLabel, RDFS.label, RPG.name, SCHEMA.name):
            aliases.update(lits(s, p))
        summary = (lits(s, DCTERMS.description) or lits(s, SCHEMA.description) or lits(s, RDFS.comment) or [""])[0]
        tags = []
        for tag in g.objects(s, RPG.hasTag):
            tl = lits(tag, SKOS.prefLabel) or lits(tag, RDFS.label)
            if tl:
                tags.append(tl[0])
        entities.append({
            "iri": str(s),
            "types": sorted(types),
            "label": label,
            "aliases": sorted(aliases),
            "summary": summary,
            "tags": sorted(tags),
        })
    entities.sort(key=lambda e: e["iri"])
    return entities, categories


_TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def build_linker(entities, exclude=frozenset()):
    """Alias matcher: multi-word aliases match case-insensitively; single-word
    aliases require exact case (so the Art 'Animal' != 'animal'). Entities in
    `exclude` (grouping/classifier nodes) are not matched in text.

    Matching is leftmost-longest and non-overlapping, one pass per case rule —
    the semantics of a longest-first regex alternation — but done by looking up
    token-aligned spans in dicts, so it stays linear in the text however many
    aliases there are (a 30k-way alternation is quadratic in practice).
    Returns (ci_map, cs_map, max_tokens)."""
    ci, cs = {}, {}   # alias-key -> set(iri)
    longest = 1
    for e in entities:
        if e["iri"] in exclude:
            continue
        for a in e["aliases"]:
            a = a.strip()
            if len(a) < 3:
                continue
            if " " in a:
                ci.setdefault(a.lower(), set()).add(e["iri"])
            else:
                cs.setdefault(a, set()).add(e["iri"])
            longest = max(longest, len(_TOKEN_RE.findall(a)))
    return (ci, cs, longest)


def _match(text, spans, table, key, longest):
    hits, i, n = set(), 0, len(spans)
    while i < n:
        found = None
        for j in range(min(n, i + longest) - 1, i - 1, -1):     # longest first
            iris = table.get(key(text[spans[i][0]:spans[j][1]]))
            if iris:
                found = j
                hits.update(iris)
                break
        i = found + 1 if found is not None else i + 1
    return hits


def link_entities(text, linker):
    ci, cs, longest = linker
    spans = [m.span() for m in _TOKEN_RE.finditer(text)]
    hits = set()
    if ci:
        hits |= _match(text, spans, ci, str.lower, longest)
    if cs:
        hits |= _match(text, spans, cs, lambda s: s, longest)
    return sorted(hits)

# ---------------------------------------------------------------------------
# Chunking (heading + word window with overlap)
# ---------------------------------------------------------------------------

def iter_sections(md_text):
    """Yield (heading_path, body_text) following markdown heading structure."""
    heading_stack = []  # list of (level, text)
    body = []
    head_re = re.compile(r"^(#{1,6})\s+(.*)$")

    def current_path():
        return [h[1] for h in heading_stack]

    for raw in md_text.splitlines():
        m = head_re.match(raw)
        if m:
            if body:
                yield current_path(), "\n".join(body)
                body = []
            level = len(m.group(1))
            heading_stack = [h for h in heading_stack if h[0] < level]
            heading_stack.append((level, clean_heading(m.group(2))))
        else:
            body.append(raw)
    if body:
        yield current_path(), "\n".join(body)


def window_words(words, target_words, overlap_words):
    if len(words) <= target_words:
        return [words]
    out, i = [], 0
    step = max(1, target_words - overlap_words)
    while i < len(words):
        out.append(words[i:i + target_words])
        if i + target_words >= len(words):
            break
        i += step
    return out


def chunk_document(md_text, slug, target_tokens, overlap_tokens, min_words):
    target_words = max(40, int(target_tokens / 1.3))
    overlap_words = max(0, int(overlap_tokens / 1.3))
    chunks, seq = [], 0
    for path, body in iter_sections(md_text):
        text = clean_text(body)
        text = re.sub(r"\n{2,}", "\n", text).strip()
        if not text:
            continue
        flat = re.sub(r"\s+", " ", text).strip()
        words = flat.split(" ")
        for win in window_words(words, target_words, overlap_words):
            if len(win) < min_words:
                continue
            wtext = " ".join(win)
            chunks.append({
                "id": "%s::%04d" % (slug, seq),
                "doc": slug,
                "seq": seq,
                "heading_path": path,
                "text": wtext,
                "n_words": len(win),
                "token_est": round(len(win) * 1.3),
            })
            seq += 1
    return chunks

# ---------------------------------------------------------------------------
# Embedding providers
# ---------------------------------------------------------------------------

def l2_normalize(vec):
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


class HashedProvider:
    """Deterministic, dependency-free feature-hashing embedding (word uni+bigrams).
    Lexical, not semantic — a reproducible fallback so the dataset builds anywhere."""
    def __init__(self, dim=256):
        self.dim = dim
        self.model = "hashed-bow-v1"

    def _hash(self, token):
        h = hashlib.md5(token.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % self.dim
        sign = 1.0 if (h[4] & 1) else -1.0
        return idx, sign

    def embed_one(self, text):
        toks = re.findall(r"[a-z0-9]+", text.lower())
        grams = toks + [toks[i] + "_" + toks[i + 1] for i in range(len(toks) - 1)]
        counts = defaultdict(int)
        for t in grams:
            counts[t] += 1
        vec = [0.0] * self.dim
        for t, c in counts.items():
            idx, sign = self._hash(t)
            vec[idx] += sign * (1.0 + math.log(c))
        return l2_normalize(vec)

    def embed(self, texts):
        return [self.embed_one(t) for t in texts]


class OllamaProvider:
    """Real local embeddings via an ollama server. No API key, no external calls.
    Uses the batched /api/embed endpoint (~30x faster than per-prompt calls)."""
    def __init__(self, model="nomic-embed-text", url="http://localhost:11434", batch=32):
        self.model = model
        self.url = url.rstrip("/")
        self.batch = batch
        self.dim = None

    def _embed_batch(self, texts):
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(self.url + "/api/embed", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        vecs = data.get("embeddings")
        if not vecs:
            raise RuntimeError("ollama returned no embeddings: %s" % data.get("error"))
        if self.dim is None:
            self.dim = len(vecs[0])
        return [l2_normalize(v) for v in vecs]

    def embed(self, texts):
        out = []
        for i in range(0, len(texts), self.batch):
            out.extend(self._embed_batch(texts[i:i + self.batch]))
            if (i // self.batch) % 5 == 4:
                print("    embedded %d/%d" % (min(i + self.batch, len(texts)), len(texts)),
                      file=sys.stderr)
        return out


def make_provider(name, model, dim, ollama_url):
    if name == "ollama":
        return OllamaProvider(model=model or "nomic-embed-text", url=ollama_url)
    if name == "hashed":
        return HashedProvider(dim=dim or 256)
    raise SystemExit("unknown provider: %s (use 'ollama' or 'hashed')" % name)

# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()

def round_vec(v, nd=6):
    return [round(x, nd) for x in v]

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=os.path.join(ROOT, "..", "Ars-Magica-Open-License"))
    ap.add_argument("--docs", nargs="*", default=DEFAULT_DOCS,
                    help="Corpus-relative markdown paths to ingest.")
    ap.add_argument("--docs-glob", default=None,
                    help="Corpus-relative glob selecting docs (e.g. 'reviewed/*.md'); overrides --docs.")
    ap.add_argument("--provider", default="ollama", choices=["ollama", "hashed"])
    ap.add_argument("--model", default="nomic-embed-text")
    ap.add_argument("--dim", type=int, default=0, help="Vector dim for the hashed provider.")
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ap.add_argument("--target-tokens", type=int, default=320)
    ap.add_argument("--overlap-tokens", type=int, default=48)
    ap.add_argument("--min-words", type=int, default=12)
    ap.add_argument("--no-entity-vectors", action="store_true",
                    help="skip embedding entity descriptions (embeddings/entities.jsonl)")
    ap.add_argument("--reuse-embeddings", action="store_true",
                    help="Reuse existing vectors (by chunk id); only embed new/changed chunks.")
    ap.add_argument("--version", default="0.5.0")
    args = ap.parse_args()

    corpus = os.path.abspath(args.corpus)
    ttl = os.path.join(ROOT, "ontology", "world.ttl")

    docs = args.docs
    if args.docs_glob:
        import glob as _glob
        docs = sorted(os.path.relpath(p, corpus)
                      for p in _glob.glob(os.path.join(corpus, args.docs_glob)))
        if not docs:
            raise SystemExit("no files matched --docs-glob %r under %s" % (args.docs_glob, corpus))

    print("[1/5] ontology -> entity index")
    entities, categories = load_entities(ttl)
    by_type = defaultdict(int)
    for e in entities:
        for t in e["types"]:
            by_type[t.rsplit("#", 1)[-1].rsplit("/", 1)[-1]] += 1
    linker = build_linker(entities, exclude=categories)
    n_alias = len(linker[0]) + len(linker[1])
    print("    %d entities (%d linkable), %d alias keys"
          % (len(entities), len(entities) - len(categories), n_alias))
    evidence = load_evidence(os.path.join(ROOT, "ontology", "_extract", "desc", "recovered.json"))
    books_path = os.path.join(ROOT, "content", "books.json")
    books = json.load(open(books_path, encoding="utf-8")) if os.path.exists(books_path) else {}

    print("[2/5] chunking %d document(s)" % len(docs))
    sources, all_chunks_by_doc = {}, {}
    mentions = defaultdict(set)   # entity iri -> set(doc slug) it is mentioned in
    for rel in docs:
        path = os.path.join(corpus, rel)
        if not os.path.exists(path):
            raise SystemExit("missing corpus file: %s" % path)
        slug = slugify(os.path.basename(rel))
        with open(path, encoding="utf-8") as f:
            md = f.read()
        chunks = chunk_document(md, slug, args.target_tokens, args.overlap_tokens, args.min_words)
        for c in chunks:
            c["entities"] = link_entities(c["text"], linker)
            for iri in c["entities"]:
                mentions[iri].add(slug)
        all_chunks_by_doc[slug] = chunks
        title = clean_heading(md.splitlines()[0]) if md.strip().startswith("#") else slug
        sources[slug] = {
            "title": title,
            "path": rel,
            "corpus_repo": CORPUS_REPO,
            "status": rel.split("/", 1)[0],
            "license": LICENSE,
            "sha256": sha256_file(path),
            "bytes": os.path.getsize(path),
            "chunks": len(chunks),
        }
        linked = sum(1 for c in chunks if c["entities"])
        print("    %-40s %4d chunks (%d entity-linked)" % (slug, len(chunks), linked))
        write_jsonl(os.path.join(ROOT, "content", "chunks", slug + ".jsonl"), chunks)

    with open(os.path.join(ROOT, "content", "sources.json"), "w", encoding="utf-8") as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)

    # Provenance: attestedIn = books whose prose mentions the entity (chunk links),
    # plus its defining books;
    # definedIn = book(s) the entity was extracted/defined from (extraction evidence).
    # Both are restricted to books actually ingested in this build.
    built_docs = set(all_chunks_by_doc)
    for e in entities:
        e["attestedIn"] = sorted(mentions.get(e["iri"], set()))
        keys = {norm_name(e["label"])} | {norm_name(a) for a in e.get("aliases", [])}
        defined = set()
        for k in keys:
            defined |= evidence.get(k, set())
        e["definedIn"] = sorted(defined & built_docs)
        # a defining book attests its entity (orp:definedIn is a sub-property of
        # orp:attestedIn), even where the alias matcher found no mention in its prose
        e["attestedIn"] = sorted(set(e["attestedIn"]) | set(e["definedIn"]))
    write_jsonl(os.path.join(ROOT, "ontology", "entities.jsonl"), entities)
    print("    provenance: %d/%d entities attested in >=1 book, %d with a defining book"
          % (sum(1 for e in entities if e["attestedIn"]), len(entities),
             sum(1 for e in entities if e["definedIn"])))

    print("[3/5] embedding (provider=%s%s)"
          % (args.provider, ", reuse" if args.reuse_embeddings else ""))
    provider = make_provider(args.provider, args.model, args.dim, args.ollama_url)
    total_vecs = 0
    for slug, chunks in all_chunks_by_doc.items():
        vec_path = os.path.join(ROOT, "embeddings", "vectors", slug + ".jsonl")
        cache = {}
        if args.reuse_embeddings and os.path.exists(vec_path):
            for r in (json.loads(l) for l in open(vec_path, encoding="utf-8") if l.strip()):
                cache[r["id"]] = r["vector"]
        need = [c for c in chunks if c["id"] not in cache]
        if need:
            for c, v in zip(need, provider.embed([c["text"] for c in need])):
                cache[c["id"]] = v
        if provider.dim is None and chunks:           # full reuse: infer dim from cache
            provider.dim = len(cache[chunks[0]["id"]])
        rows = [{"id": c["id"], "vector": round_vec(cache[c["id"]])} for c in chunks]
        write_jsonl(vec_path, rows)
        total_vecs += len(rows)
        print("    %-40s %4d vectors (dim=%d, %d reused)"
              % (slug, len(rows), provider.dim, len(rows) - len(need)))

    # Entity vectors: one embedding per entity of "label (type): summary", so a
    # question that describes a thing without naming it can find the thing
    # (ontorag-mcp `entity` retrieval). Reused when the text is unchanged.
    ent_vec_rel = None
    if not args.no_entity_vectors:
        ent_dir = os.path.join(ROOT, "embeddings", "entities")
        meta_path = os.path.join(ROOT, "embeddings", "entities.meta.json")
        shard_of = lambda iri: "%s.jsonl" % hashlib.sha1(iri.encode("utf-8")).hexdigest()[0]  # 16 shards
        def ent_text(e):
            typ = (e.get("tags") or [re.split(r"[/#]", t)[-1] for t in e.get("types", [])] or [""])[0]
            return "%s (%s): %s" % (e["label"], typ, e.get("summary") or "")
        texts = {e["iri"]: ent_text(e) for e in entities if e.get("summary")}
        digest = {i: hashlib.sha1(t.encode("utf-8")).hexdigest()[:16] for i, t in texts.items()}
        cache = {}
        old_files = sorted(glob.glob(os.path.join(ent_dir, "*.jsonl")))
        legacy = os.path.join(ROOT, "embeddings", "entities.jsonl")      # pre-sharding layout
        if os.path.exists(legacy):
            old_files.append(legacy)
        if old_files and os.path.exists(meta_path):
            old = json.load(open(meta_path, encoding="utf-8"))
            for f in old_files:
                for r in (json.loads(l) for l in open(f, encoding="utf-8") if l.strip()):
                    if old.get(r["id"]) == digest.get(r["id"]):
                        cache[r["id"]] = r["vector"]
        need = [i for i in sorted(texts) if i not in cache]
        if need:
            for i, v in zip(need, provider.embed([texts[i] for i in need])):
                cache[i] = v
        os.makedirs(ent_dir, exist_ok=True)
        shards = defaultdict(list)
        for i in sorted(texts):
            shards[shard_of(i)].append({"id": i, "vector": round_vec(cache[i])})
        for f in glob.glob(os.path.join(ent_dir, "*.jsonl")):
            if os.path.basename(f) not in shards:
                os.remove(f)
        for name, rows in shards.items():
            write_jsonl(os.path.join(ent_dir, name), rows)
        if os.path.exists(legacy):
            os.remove(legacy)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({i: digest[i] for i in sorted(texts)}, f)
        ent_vec_rel = "embeddings/entities/*.jsonl"
        print("    entities: %d vectors (%d reused)" % (len(texts), len(texts) - len(need)))

    print("[4/5] embeddings/config.json")
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    emb_cfg = {
        "provider": args.provider,
        "model": provider.model,
        "dim": provider.dim,
        "metric": "cosine",
        "normalized": True,
        "input": "chunk.text",
        "built_at": now,
        "note": "Query vectors MUST be produced with this same provider+model and L2-normalized.",
    }
    with open(os.path.join(ROOT, "embeddings", "config.json"), "w", encoding="utf-8") as f:
        json.dump(emb_cfg, f, ensure_ascii=False, indent=2)

    print("[5/5] manifest.json")
    total_chunks = sum(len(c) for c in all_chunks_by_doc.values())
    manifest = {
        "ontorag": "0.1",
        "dataset": {
            "id": "amol",
            "name": "Ars Magica Open License — OntoRAG",
            "version": args.version,
            "license": LICENSE,
            "source_repo": CORPUS_REPO,
            "built_at": now,
            "builder": "tools/build.py",
        },
        "ontology": {
            "format": "text/turtle",
            "graph": "ontology/world.ttl",
            "entity_index": "ontology/entities.jsonl",
            "prefixes": "ontology/prefixes.json",
            "base_iri": "https://www.fantasymaps.org/amol-ontorag/id/",
            "aligns_with": [
                {"slug": "rpg", "role": "schema"},
                {"slug": "schemaorg", "role": "vocabulary"},
                {"slug": "dublincore", "role": "vocabulary"},
                {"slug": "foaf", "role": "vocabulary"},
            ],
            "counts": {"entities": len(entities), "by_type": dict(sorted(by_type.items()))},
            "provenance": {
                "vocabulary": "https://ontorag.org/provenance#",
                "attested_in": "entities[].attestedIn / orp:attestedIn — books whose prose mentions the entity",
                "defined_in": "entities[].definedIn / orp:definedIn — book(s) the entity was extracted from",
                "book_requires": "orp:Source dcterms:requires, mirrored by orp:Pack orp:requires — inter-book dependency graph",
                "graph_block": "tools/provenance.py (regenerable block in world.ttl)",
            },
        },
        "content": {
            "format": "application/x-ndjson",
            "chunks_glob": "content/chunks/*.jsonl",
            "sources": "content/sources.json",
            "books": "content/books.json",
            "chunking": {
                "strategy": "heading+window",
                "target_tokens": args.target_tokens,
                "overlap_tokens": args.overlap_tokens,
                "token_heuristic": "words*1.3",
            },
            "counts": {"documents": len(all_chunks_by_doc), "chunks": total_chunks,
                       "books": len(books)},
        },
        "embeddings": {
            "config": "embeddings/config.json",
            "vectors_glob": "embeddings/vectors/*.jsonl",
            "provider": args.provider,
            "model": provider.model,
            "dim": provider.dim,
            "metric": "cosine",
            "normalized": True,
            "alignment": "by_id",
            "counts": {"vectors": total_vecs},
        },
        "schema": {
            "manifest": SPEC + "manifest.schema.json",
            "chunk": SPEC + "chunk.schema.json",
            "embedding": SPEC + "embedding.schema.json",
            "entity": SPEC + "entity.schema.json",
        },
    }
    if ent_vec_rel:
        manifest["embeddings"]["entity_vectors_glob"] = ent_vec_rel
        manifest["embeddings"]["counts"]["entity_vectors"] = sum(
            1 for f in glob.glob(os.path.join(ROOT, ent_vec_rel))
            for l in open(f, encoding="utf-8") if l.strip())
    # Composition model: register the pack (sourcebook) system if provenance exists.
    _books_path = os.path.join(ROOT, "content", "books.json")
    if os.path.exists(_books_path):
        _books = json.load(open(_books_path, encoding="utf-8"))
        manifest["composition"] = {
            "model": "packs",
            "version": "0.1",
            "unit": "sourcebook",
            "registry": "content/books.json",
            "dependency": "orp:requires",
            "core": sorted(p for p, m in _books.items() if not m.get("requires")),
            "scope": "chunks/vectors are in scope iff their book is in the selected "
                     "packs; an entity is in scope iff its attestedIn intersects them, "
                     "or it is a spine entity (definedIn empty). For access control use "
                     "the held packs as they are; for a coherent world, close them over "
                     "orp:requires first. Rules: https://ontorag.org/provenance/#packs",
            "tool": "tools/compose.py",
            "counts": {"packs": len(_books)},
        }
    with open(os.path.join(ROOT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\nDone: %d entities, %d chunks, %d vectors (dim=%d, provider=%s)."
          % (len(entities), total_chunks, total_vecs, provider.dim, args.provider))


if __name__ == "__main__":
    main()
