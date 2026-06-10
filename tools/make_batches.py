#!/usr/bin/env python3
"""
make_batches.py — slice the chunk corpus into self-contained batch files for the
Claude entity-extraction workflow (tools/extract_ontology.workflow.js).

Each batch file holds a handful of chunks (id + heading_path + text only) so one
extraction agent can read a single small file and extract entities from it.
Writes batches under ontology/_extract/batches/ and an index batches.json
(absolute paths) that the workflow receives via `args`.
"""
import argparse, glob, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=120, help="chunks per batch")
    ap.add_argument("--out", default=os.path.join(ROOT, "ontology", "_extract"))
    args = ap.parse_args()

    batch_dir = os.path.join(args.out, "batches")
    os.makedirs(batch_dir, exist_ok=True)
    for f in glob.glob(os.path.join(batch_dir, "*.jsonl")):
        os.remove(f)

    manifest = json.load(open(os.path.join(ROOT, "manifest.json")))
    files = sorted(glob.glob(os.path.join(ROOT, manifest["content"]["chunks_glob"])))

    index, total = [], 0
    for path in files:
        slug = os.path.basename(path)[:-6]  # strip .jsonl
        chunks = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        total += len(chunks)
        for i in range(0, len(chunks), args.batch):
            grp = chunks[i:i + args.batch]
            bid = "%s__%03d" % (slug, i // args.batch)
            bpath = os.path.join(batch_dir, bid + ".jsonl")
            with open(bpath, "w", encoding="utf-8") as f:
                for c in grp:
                    f.write(json.dumps({"id": c["id"],
                                        "heading_path": c.get("heading_path", []),
                                        "text": c["text"]}, ensure_ascii=False) + "\n")
            index.append({"batch_id": bid, "path": os.path.abspath(bpath), "n": len(grp)})

    with open(os.path.join(args.out, "batches.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print("%d chunks -> %d batches of <=%d  (index: %s)"
          % (total, len(index), args.batch, os.path.join(args.out, "batches.json")))


if __name__ == "__main__":
    main()
