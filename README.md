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
│   └── chunks/<doc>.jsonl     ← retrievable text units, each linked to entity IRIs
├── embeddings/
│   ├── config.json            ← provider, model, dim, metric, normalized
│   └── vectors/<doc>.jsonl    ← {id, vector}, joined to chunks by id
├── schema/*.schema.json       ← JSON Schemas for manifest / chunk / embedding / entity
├── tools/
│   ├── build.py               ← corpus → ontology index → chunks → embeddings → manifest
│   ├── query.py               ← reference connector / retrieval demo
│   ├── Dockerfile             ← modern pinned Python for the tooling
│   └── requirements.txt
└── docker-compose.yml         ← `build` and `query` services
```

Every record type has a JSON Schema in `schema/`, referenced from `manifest.json`.
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

## What's in the sample

The committed sample ingests two reviewed sourcebooks — *Houses of Hermes: True
Lineages* and *Houses of Hermes: Mystery Cults* — which between them reference 8
of the 12 Hermetic Houses, so entity linking and cross-document graph expansion
are both exercised. The ontology covers the World (Mythic Europe), the ArM5
ruleset, the 15 Hermetic Arts, all 12 Houses, the 4 Realms of Power, and a
selection of Tribunals. Re-run `build.py` with `--docs` to ingest more books.

## License

The described **content** is © 1993–2024 Trident, Inc. d/b/a Atlas Games,
released under **[CC-BY-SA-4.0](https://creativecommons.org/licenses/by-sa/4.0/)**
(the Ars Magica Open License). Derived chunks/embeddings inherit CC-BY-SA-4.0; see
`content/sources.json` and `LICENSE.md`. The structure, schemas and tooling in
this repository are provided as a sample you may reuse freely.
