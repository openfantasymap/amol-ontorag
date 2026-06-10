# Content layer

The retrievable text, chunked from the source corpus and linked to ontology entities.

| File | Role |
|------|------|
| `sources.json` | Provenance for every ingested document: corpus-relative path, upstream repo, review `status`, `sha256`, byte size, chunk count, license. A consumer can re-fetch or verify the original against this. |
| `chunks/<doc>.jsonl` | One **chunk** per line (NDJSON). The retrievable unit of text. |

The source markdown itself is **not** duplicated here — the chunk text is the
derived, retrieval-ready copy, and `sources.json` points back to the upstream
files (with checksums) in `../Ars-Magica-Open-License`.

## Chunk record shape

See [`../schema/chunk.schema.json`](../schema/chunk.schema.json):

```json
{"id":"houses-of-hermes-true-lineages::0042","doc":"houses-of-hermes-true-lineages","seq":42,
 "heading_path":["Houses of Hermes: True Lineages","Chapter Four: House Tremere","Certamen"],
 "text":"…","n_words":238,"token_est":309,
 "entities":["https://ontorag.dev/amol/HouseTremere"]}
```

- **`id`** — globally unique; the join key to the embedding record. Convention `<doc_slug>::<seq>`.
- **`heading_path`** — markdown heading breadcrumb (H1…Hn), useful for display and re-ranking.
- **`entities`** — ontology IRIs mentioned in the chunk (the OntoRAG link).

## Chunking strategy

`heading+window` (see `manifest.content.chunking`): the document is split along
markdown headings; long sections are windowed to a target token budget with
overlap so no passage is cut mid-thought without context. Token counts are
estimated as `words × 1.3`. Re-tune with `build.py --target-tokens / --overlap-tokens`.

## Adding documents

```bash
python3 tools/build.py --docs \
  "reviewed/Ars Magica 5e - Houses of Hermes - Societates.md" \
  "reviewed/Ars Magica 5e - Realms of Power - Magic.md"
```

Slugs are derived from filenames (the `Ars Magica 5e - ` prefix is stripped).
Bump `dataset.version` when the chunk set changes.
