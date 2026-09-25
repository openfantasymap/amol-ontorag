#!/usr/bin/env python3
"""
merge_extract.py — fold additional extraction output into the consolidated shard.

The June 2026 extraction (extract_ontology.workflow.js) stopped after 66 of 163
batches; what it produced is kept in ontology/_extract/june/recovered.json. The
remaining 97 batches (17 books) were extracted in September 2026, one JSON array per
batch in ontology/_extract/raw2/<batch_id>.json (same record shape: name, type,
aliases, description, evidence). This merges both through the
same conservative dedupe as recover_extract.py, so entities already in the graph
keep their canonical names, and writes recovered.json back for ttl_from_entities.py.

    python3 tools/merge_extract.py            # june/recovered.json + raw2/*.json -> desc/recovered.json
"""
import argparse, glob, json, os
from collections import Counter

from recover_extract import aggregate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACT = os.path.join(ROOT, "ontology", "_extract")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.path.join(EXTRACT, "june", "recovered.json"))
    ap.add_argument("--raw", default=os.path.join(EXTRACT, "raw2"))
    ap.add_argument("--out", default=os.path.join(EXTRACT, "desc", "recovered.json"))
    ap.add_argument("--max-evidence", type=int, default=8)
    args = ap.parse_args()

    base = json.load(open(args.base, encoding="utf-8"))
    mentions = [dict(r, _base=True) for r in base]
    files = sorted(glob.glob(os.path.join(args.raw, "*.json")))
    bad = []
    for f in files:
        try:
            recs = json.load(open(f, encoding="utf-8"))
        except json.JSONDecodeError:
            bad.append(os.path.basename(f))
            continue
        mentions.extend(r for r in recs if isinstance(r, dict) and r.get("name"))
    print("base: %d entities; %d batch files (%d unreadable: %s); %d mentions in total"
          % (len(base), len(files), len(bad), ", ".join(bad) or "-", len(mentions)))

    # keep only evidence that names a real chunk (an agent occasionally mistypes one)
    valid = {json.loads(l)["id"] for f in glob.glob(os.path.join(ROOT, "content", "chunks", "*.jsonl"))
             for l in open(f, encoding="utf-8") if l.strip()}
    dropped = 0
    for m in mentions:
        ev = [x for x in (m.get("evidence") or []) if x in valid]
        dropped += len(m.get("evidence") or []) - len(ev)
        m["evidence"] = ev
    print("dropped %d evidence ids that name no chunk" % dropped)

    out = aggregate(mentions, max_evidence=args.max_evidence)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    books = Counter(x.split("::")[0] for e in out for x in e["evidence"])
    print("=> %d unique entities (+%d) -> %s" % (len(out), len(out) - len(base), args.out))
    print("   books with extracted evidence: %d" % len(books))
    print("   by type:", dict(Counter(e["type"] for e in out).most_common()))


if __name__ == "__main__":
    main()
