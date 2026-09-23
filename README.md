# amol-ontorag — Ars Magica Open License, as an OntoRAG GitHub-as-storage dataset

A **reusable, self-describing repository layout** that a retrieval service (an
"OntoRAG" engine) can clone and immediately use to answer questions, grounded in:

- an **ontology** (a knowledge graph of the domain), and
- **content** (the source text, chunked for retrieval), and
- **embeddings** (vectors for semantic search),

all derived from the [Ars Magica Open License corpus](https://github.com/OriginalMadman/Ars-Magica-Open-License)
(`../Ars-Magica-Open-License`).

The git repository **is** the database. There is no server to provision: a
consumer reads `manifest.json`, follows the paths it declares, and starts
retrieving. Everything is plain text / NDJSON / Turtle — diffable, versionable,
and host-agnostic.

> **OntoRAG = Ontology + RAG.** Plain RAG retrieves text by vector similarity.
> OntoRAG additionally links every chunk to typed entities in a knowledge graph,
> so retrieval can be *graph-aware*: expand a hit to its siblings, filter by
> entity type, or inject structured facts alongside the prose.

---

## Layout (the contract)

```
amol-ontorag/
├── manifest.json              ← entry point: versions, paths, counts, embedding model
├── ontology/
│   ├── world.ttl              ← the knowledge graph (Turtle) — SOURCE OF TRUTH
│   ├── entities.jsonl         ← flat entity index derived from world.ttl (fast load + linking)
│   └── prefixes.json          ← namespace prefixes (amol:, rpg:, schema:, …)
├── content/
│   ├── sources.json           ← provenance: which corpus files, sha256, license, status
│   ├── books.json             ← pack registry: one pack per book, with `requires`
│   └── chunks/<book>.jsonl    ← retrievable text units, each linked to entity IRIs
├── embeddings/
│   ├── config.json            ← provider, model, dim, metric, normalized
│   └── vectors/<book>.jsonl   ← {id, vector}, joined to chunks by id
├── docs/composition.md        ← packs: selecting books for access scope or composition
├── tools/
│   ├── build.py               ← corpus → ontology index → chunks → embeddings → manifest
│   ├── provenance.py          ← book provenance (orp:) block of world.ttl
│   ├── compose.py             ← validate the dataset; compose or scope packs
│   ├── query.py               ← reference connector / retrieval demo
│   ├── Dockerfile             ← modern pinned Python for the tooling
│   └── requirements.txt
└── docker-compose.yml         ← `build` and `query` services
```

The layout follows the **OntoRAG dataset format 0.1**
(<https://ontorag.org/vocab/#format>). Every record type has a canonical JSON
Schema at `https://ontorag.org/vocab/dataset/0.1/`, referenced from
`manifest.json`. Book provenance, citations and packs use the **OntoRAG Provenance
and Citation Ontology** (`orp:`, <https://ontorag.org/provenance/>). Entity IRIs
live under `https://www.fantasymaps.org/amol-ontorag/id/`, and classes align with
[rpg-schema](https://www.rpg-schema.org/) (`http://www.rpg-schema.org/1.0/`).

The three data layers join on stable ids:

```
chunk.id  ──────────────  embedding.id          (content ↔ vectors)
chunk.entities[]  ──────  entity.iri            (content ↔ ontology)
```

## How a service connects (the 7-step contract)

1. Read **`manifest.json`** → discover layer paths + the embedding `provider/model/dim/metric`.
2. Load **`ontology/entities.jsonl`** (or `world.ttl` into a triplestore) → the graph lens.
3. Load **`content/chunks/*.jsonl`** + **`embeddings/vectors/*.jsonl`** → the corpus, joined by `id`.
4. Embed the user's query with the **same** provider+model the manifest declares.
5. **Cosine** top-k retrieval (vectors are L2-normalized → dot == cosine).
6. **Graph expansion**: collect entities linked from the top hits, pull in sibling
   chunks that share them.
7. Assemble a **grounded, cited context** (ontology facts + passages) and hand it to an LLM.

`tools/query.py` implements exactly these steps as a runnable reference.

## Quick start (Docker — recommended)

The host Python may be old; the tooling runs on a pinned Python 3.12 image and
produces embeddings by talking to a local **[ollama](https://ollama.com)** server
(model `nomic-embed-text`, 768-dim) — no API key required.

```bash
# one-time: have an ollama server running with the embed model
ollama pull nomic-embed-text          # or: curl localhost:11434/api/pull -d '{"name":"nomic-embed-text"}'

docker compose build                  # build the tooling image
docker compose run --rm build         # (re)generate ontology index, chunks, vectors, manifest
docker compose run --rm query tools/query.py "How does House Tremere use certamen?"
```

## Quick start (no Docker, fully offline)

The pipeline also runs on stock Python with the dependency-free `hashed`
embedding provider (lexical, deterministic — for when you have neither ollama nor
a model API). Retrieval quality is lower but the dataset builds anywhere:

```bash
pip install rdflib
python3 tools/build.py --provider hashed --dim 256
python3 tools/query.py "What is a heartbeast?"
```

## Swapping in production embeddings

The embedding layer is pluggable; the manifest records exactly what was used so a
consumer embeds queries compatibly. See [`embeddings/README.md`](embeddings/README.md).
For a hosted option, Anthropic recommends **Voyage AI** (Claude has no embeddings
API); `voyageai`/`openai`/`sentence-transformers` are listed in
`tools/requirements.txt`. Generation (the final answer) is left to the caller — a
Claude model such as `claude-opus-4-8` or `claude-sonnet-4-6` is a natural fit.

## What's in the dataset

Version 0.5.0 holds **25 reviewed sourcebooks**: the Definitive Edition core rules
plus 24 supplements. From them the build produces:

- **17,942 chunks**, each with a 768-dimension `nomic-embed-text` vector;
- **3,866 entities**: tags, characters, spells, places, factions, creatures, items
  and proficiencies, over a curated spine (Mythic Europe, the ArM5 rule set, the 15
  Hermetic Arts, the 12 Houses, the 4 Realms, the Tribunals).

Each book is a pack, so you can scope retrieval or composition to a subset of books.
See [`docs/composition.md`](docs/composition.md). Counts come from `manifest.json`.

## License

The described **content** is © 1993–2024 Trident, Inc. d/b/a Atlas Games,
released under **[CC-BY-SA-4.0](https://creativecommons.org/licenses/by-sa/4.0/)**
(the Ars Magica Open License). Derived chunks/embeddings inherit CC-BY-SA-4.0; see
`content/sources.json` and `LICENSE.md`. The structure, schemas and tooling in
this repository are provided as a sample you may reuse freely.
