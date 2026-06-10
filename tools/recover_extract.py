#!/usr/bin/env python3
"""
recover_extract.py — salvage entity extractions from a (partially failed) run of
extract_ontology.workflow.js by reading the per-agent transcripts.

Each schema-forced extract agent emitted a StructuredOutput tool call whose input
is {entities:[...]}. We harvest those, aggregate/dedupe by normalized name, choose
the best available description (extractive — no further LLM calls), and write a
single consolidated shard that tools/ttl_from_entities.py consumes.

This makes the expensive extraction recoverable even when the Describe phase or
later batches die (e.g. on an account session limit).
"""
import argparse, glob, json, os, re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", re.sub(r"^the\s+", "", s.lower()))).strip()


def iter_tool_inputs(path):
    """Yield dict inputs of any tool_use found in an agent transcript."""
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                yield block["input"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts", required=True, help="workflow transcript dir (agent-*.jsonl)")
    ap.add_argument("--out", default=os.path.join(ROOT, "ontology", "_extract", "desc", "recovered.json"))
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.transcripts, "agent-*.jsonl")))
    mentions, agents_with_entities = [], 0
    for f in files:
        got = False
        for inp in iter_tool_inputs(f):
            ents = inp.get("entities")
            if isinstance(ents, list) and ents and isinstance(ents[0], dict) and "name" in ents[0]:
                mentions.extend(ents)
                got = True
        if got:
            agents_with_entities += 1

    print("scanned %d agent transcripts; %d carried entities; %d raw mentions"
          % (len(files), agents_with_entities, len(mentions)))

    agg = {}
    for m in mentions:
        name = (m.get("name") or "").strip()
        if not name or not re.search(r"[A-Za-z]", name):
            continue
        k = norm(name)
        if len(k) < 2:
            continue
        e = agg.get(k)
        if not e:
            e = {"names": Counter(), "types": Counter(), "aliases": set(), "descs": [], "evidence": set()}
            agg[k] = e
        e["names"][name] += 1
        if m.get("type"):
            e["types"][m["type"]] += 1
        for a in (m.get("aliases") or []):
            if a and len(a) <= 60:
                e["aliases"].add(a.strip())
        d = (m.get("description") or "").strip()
        if d:
            e["descs"].append(re.sub(r"\s+", " ", d))
        for x in (m.get("evidence") or []):
            if isinstance(x, str):
                e["evidence"].add(x)

    # --- precise canonicalization: merge entity A into B when A lists B's
    #     canonical name as one of its surface forms (alias/name). Conservative
    #     (no LLM): only merges on an exact normalized name match, length >= 4.
    parent = {k: k for k in agg}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    name_to_key = {}
    for k, e in agg.items():
        pn = norm(e["names"].most_common(1)[0][0])
        name_to_key.setdefault(pn, k)
    for k, e in agg.items():
        surfaces = set(e["names"]) | e["aliases"]
        for a in surfaces:
            na = norm(a)
            if len(na) >= 4 and na in name_to_key and name_to_key[na] != k:
                union(k, name_to_key[na])

    clusters = defaultdict(list)
    for k in agg:
        clusters[find(k)].append(k)
    merged = {}
    for root, members in clusters.items():
        M = {"names": Counter(), "types": Counter(), "aliases": set(), "descs": [], "evidence": set()}
        for k in members:
            e = agg[k]
            M["names"].update(e["names"]); M["types"].update(e["types"])
            M["aliases"] |= e["aliases"]; M["descs"] += e["descs"]; M["evidence"] |= e["evidence"]
        merged[root] = M
    print("merged %d -> %d entities via exact alias=name canonicalization"
          % (len(agg), len(merged)))
    agg = merged

    out = []
    for k, e in agg.items():
        name = e["names"].most_common(1)[0][0]
        typ = e["types"].most_common(1)[0][0] if e["types"] else "Concept"
        # extractive description: the longest candidate (most informative), capped
        desc = ""
        if e["descs"]:
            desc = max(e["descs"], key=len)[:500]
        aliases = sorted({a for a in e["aliases"] if norm(a) != norm(name)})[:8]
        out.append({
            "name": name, "type": typ, "aliases": aliases,
            "description": desc, "evidence": sorted(e["evidence"])[:5],
            "_mentions": sum(e["names"].values()),
        })
    out.sort(key=lambda x: (-x["_mentions"], x["name"]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    by_type = Counter(e["type"] for e in out)
    print("=> %d unique entities -> %s" % (len(out), args.out))
    print("   by type:", dict(by_type.most_common()))
    print("   sample:", "; ".join(e["name"] for e in out[:12]))


if __name__ == "__main__":
    main()
