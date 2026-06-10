# Embeddings layer

Vectors for semantic retrieval, joined to chunks by `id`.

| File | Role |
|------|------|
| `config.json` | The embedding contract: `provider`, `model`, `dim`, `metric`, `normalized`. A consumer **must** embed queries with this same provider+model. |
| `vectors/<doc>.jsonl` | One `{ "id": ..., "vector": [...] }` per line; `id` == the chunk id. |

Vectors are **L2-normalized**, so cosine similarity equals the dot product.
Each `vector` length equals `config.dim`. Values are rounded to 6 decimals to keep
the NDJSON compact and the git history readable.

## Providers

The pipeline ships two zero-config providers so it builds anywhere, plus drop-in
hooks for hosted/local models:

| provider | model | dim | needs | notes |
|----------|-------|-----|-------|-------|
| `ollama` *(default)* | `nomic-embed-text` | 768 | a local ollama server | real semantic vectors, **no API key** |
| `hashed` | `hashed-bow-v1` | configurable (256) | nothing (stdlib) | deterministic *lexical* fallback; offline & reproducible |
| `voyageai` | `voyage-3*` | 1024+ | `VOYAGE_API_KEY` | Anthropic-recommended hosted embeddings |
| `openai` | `text-embedding-3-*` | 1536/3072 | `OPENAI_API_KEY` | |
| `sentence-transformers` | e.g. `bge-small-en-v1.5` | 384 | downloads weights | fully local |

> Claude (Anthropic) has **no embeddings endpoint** — Claude is the *generation*
> model. Retrieval uses one of the embedders above; the manifest records which.

## Choosing / switching providers

```bash
# default: real local semantic embeddings via ollama
docker compose run --rm build                 # provider=ollama, model=nomic-embed-text

# offline, dependency-free
python3 tools/build.py --provider hashed --dim 256
```

To add `voyageai`/`openai`/`sentence-transformers`: uncomment it in
`tools/requirements.txt`, add a small provider class in `tools/build.py` (mirror
`OllamaProvider`) and the matching `embed_*` in `tools/query.py`, then rebuild.
**Whenever the provider, model or dim changes, every vector must be regenerated**
(query and index embeddings must come from the same model) — `build.py` rewrites
`config.json` and `manifest.json` to match.

## Storage at scale

For larger corpora, JSONL stays diff-friendly but grows; consider Git LFS for the
`vectors/` directory, or a binary sidecar (`.npy`/Parquet) with this JSONL kept as
the canonical, inspectable form. The `vectors_glob` in the manifest is the only
thing a consumer relies on.
