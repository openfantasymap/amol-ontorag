#!/usr/bin/env python3
"""
query.py — reference OntoRAG connector / retrieval demo.

Shows the contract a service like `ontorag` follows to "connect and start working"
against this GitHub-as-storage dataset:

  1. read manifest.json                -> discover layout + embedding model/dim/metric
  2. load entities.jsonl               -> the ontology lens (no SPARQL engine needed)
  3. load content/chunks + embeddings  -> the retrieval corpus
  4. embed the query with the SAME provider+model declared in the manifest
  5. cosine top-k retrieval
  6. graph expansion: pull in sibling chunks that share linked ontology entities
  7. assemble a grounded, cited context block ready to hand to an LLM (e.g. Claude)

The final answer generation step is intentionally left to the caller: feed the
printed CONTEXT to your LLM of choice. (Anthropic's Claude — e.g. claude-opus-4-8
or claude-sonnet-4-6 — is a natural fit; Claude has no embeddings API, so retrieval
uses the local embedder declared in the manifest.)

Usage:
    python3 tools/query.py "How does House Tremere use certamen?"
    python3 tools/query.py --k 6 --expand 4 "What is a heartbeast?"
"""
import argparse
import glob
import json
import math
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def l2_normalize(vec):
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


# --- embedding providers (mirror tools/build.py; query must match the index) ---

def embed_ollama(text, model, url):
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/api/embeddings", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return l2_normalize(data["embedding"])


def embed_hashed(text, dim):
    import hashlib
    from collections import defaultdict
    toks = re.findall(r"[a-z0-9]+", text.lower())
    grams = toks + [toks[i] + "_" + toks[i + 1] for i in range(len(toks) - 1)]
    counts = defaultdict(int)
    for t in grams:
        counts[t] += 1
    vec = [0.0] * dim
    for t, c in counts.items():
        h = hashlib.md5(t.encode()).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign * (1.0 + math.log(c))
    return l2_normalize(vec)


def embed_query(text, emb_cfg, ollama_url):
    if emb_cfg["provider"] == "ollama":
        return embed_ollama(text, emb_cfg["model"], ollama_url)
    if emb_cfg["provider"] == "hashed":
        return embed_hashed(text, emb_cfg["dim"])
    raise SystemExit("unknown embedding provider in config: %s" % emb_cfg["provider"])


def cosine(a, b):  # vectors are stored L2-normalized -> dot == cosine
    return sum(x * y for x, y in zip(a, b))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=5, help="top-k by vector similarity")
    ap.add_argument("--expand", type=int, default=3, help="extra chunks via shared entities")
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    args = ap.parse_args()

    manifest = json.load(open(os.path.join(ROOT, "manifest.json")))
    emb_cfg = json.load(open(os.path.join(ROOT, manifest["embeddings"]["config"])))
    entities = {e["iri"]: e for e in load_jsonl(os.path.join(ROOT, manifest["ontology"]["entity_index"]))}

    # load corpus (chunks joined to vectors by id)
    chunks = {}
    for path in glob.glob(os.path.join(ROOT, manifest["content"]["chunks_glob"])):
        for c in load_jsonl(path):
            chunks[c["id"]] = c
    vectors = {}
    for path in glob.glob(os.path.join(ROOT, manifest["embeddings"]["vectors_glob"])):
        for v in load_jsonl(path):
            vectors[v["id"]] = v["vector"]

    print("connected: %d chunks, %d vectors, %d entities | embed=%s/%s dim=%d\n"
          % (len(chunks), len(vectors), len(entities),
             emb_cfg["provider"], emb_cfg["model"], emb_cfg["dim"]), file=sys.stderr)

    qv = embed_query(args.query, emb_cfg, args.ollama_url)
    scored = sorted(((cosine(qv, vectors[cid]), cid) for cid in chunks if cid in vectors),
                    reverse=True)
    top = scored[:args.k]

    # graph expansion: entities mentioned by the top hits -> sibling chunks
    seed_ents, chosen = set(), set(cid for _, cid in top)
    for _, cid in top:
        seed_ents.update(chunks[cid].get("entities", []))
    expanded = []
    if args.expand and seed_ents:
        cand = []
        for cid, c in chunks.items():
            if cid in chosen:
                continue
            overlap = len(set(c.get("entities", [])) & seed_ents)
            if overlap:
                cand.append((overlap, cosine(qv, vectors.get(cid, [0] * emb_cfg["dim"])), cid))
        cand.sort(reverse=True)
        expanded = [(s, c) for _, s, c in cand[:args.expand]]

    def show(score, cid, tag):
        c = chunks[cid]
        ents = ", ".join(entities[i]["label"] for i in c.get("entities", []) if i in entities) or "—"
        crumb = " › ".join(c["heading_path"][-3:]) or "(root)"
        snippet = re.sub(r"\s+", " ", c["text"])[:240]
        print("  [%s %.3f] %s" % (tag, score, c["doc"]))
        print("    %s" % crumb)
        print("    entities: %s" % ents)
        print("    %s…\n" % snippet)

    print("== QUERY ==\n  %s\n" % args.query)
    print("== TOP-K (vector) ==")
    for s, cid in top:
        show(s, cid, "sim")
    if expanded:
        print("== GRAPH-EXPANDED (shared ontology entities: %s) =="
              % ", ".join(sorted(entities[i]["label"] for i in seed_ents if i in entities)))
        for s, cid in expanded:
            show(s, cid, "rel")

    # assemble the context block an LLM would receive
    print("== CONTEXT (feed to your LLM; cite by [doc::seq]) ==")
    used = [cid for _, cid in top] + [cid for _, cid in expanded]
    facts = sorted({i for cid in used for i in chunks[cid].get("entities", [])})
    if facts:
        print("# Ontology facts")
        for i in facts:
            e = entities.get(i)
            if e:
                print("- %s (%s): %s" % (e["label"], "/".join(e["tags"]) or "entity", e["summary"]))
    print("\n# Passages")
    for cid in used:
        c = chunks[cid]
        print("[%s] %s" % (cid, re.sub(r"\s+", " ", c["text"])))
    print("\n(Generation step: send the above CONTEXT + the QUERY to Claude and ask it "
          "to answer using only these passages, citing [doc::seq].)")


if __name__ == "__main__":
    main()
