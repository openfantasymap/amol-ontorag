# Ontology layer

The knowledge graph that makes retrieval *graph-aware*.

| File | Role |
|------|------|
| `world.ttl` | **Source of truth.** The domain knowledge graph in Turtle. Hand-authored spine + generated blocks. Loadable into [Oxigraph](https://github.com/oxigraph/oxigraph), `rdflib`, or any RDF store. |
| `entities.jsonl` | A flat projection of `world.ttl` (one entity per line), **generated** by `tools/build.py`. Lets a consumer load the graph lens and build the chunk-linking dictionary without a SPARQL engine. |
| `prefixes.json` | Namespace prefixes used in `world.ttl`. |

`world.ttl` is assembled in three parts: the **curated spine** (hand-authored), the
**extracted-entities** block (`tools/ttl_from_entities.py`), and the **provenance**
block (`tools/provenance.py`). Each generated block is regenerable and delimited by
a `# ============ BEGIN … ============` marker; never hand-edit inside one.

## Book provenance

Each entity records *where it comes from*, so a consumer can reconstruct "the world
as known from a subset of books" (e.g. for access-scoping or a per-book
needle-in-a-haystack eval):

- **`entities[].attestedIn`** — book slugs whose prose *mentions* the entity
  (derived from chunk↔entity links). The entity's footprint.
- **`entities[].definedIn`** — book slug(s) the entity was *extracted/defined from*
  (from the extraction shard's `evidence` chunks). The entity's origin; empty for
  hand-curated spine entities not present in the extraction.

In the graph these become first-class edges, using the OntoRAG Provenance and
Citation Ontology (`orp:`, <https://ontorag.org/provenance/>):
`amol:<Entity> orp:attestedIn <…/id/source/<slug>>` and `orp:definedIn`.

- Each book is an `orp:Source` (`dcterms:title`, `dcterms:identifier` = slug).
- Its Markdown file is an `orp:SourceFile` with `orp:checksum "sha256:…"`.
- Its slice of the dataset is an `orp:Pack` (registry:
  [`../content/books.json`](../content/books.json)).
- Supplements `dcterms:requires` the core rules, mirrored by `orp:requires` between
  their packs. The shared vocabulary is the `orp:SpinePack`.

To reconstruct a scoped world, select packs and filter on `orp:attestedIn`. See
[`../docs/composition.md`](../docs/composition.md) for why access scoping must not
close over `orp:requires` while composition does.

## Schema alignment

Classes/properties prefixed `rpg:` align with the **rpg-schema** TTRPG ontology
(catalog slug `rpg`): `rpg:World`, `rpg:RuleSet`, `rpg:Faction`,
`rpg:Proficiency`, `rpg:Tag`, with `rpg:inWorld`, `rpg:hasTag`,
`rpg:capabilityDefinedInRuleSet`, etc. Standard `schema:`, `dc:`, `foaf:` and
`skos:` vocabularies are used for labels, descriptions and provenance. All
instance data lives under the `amol:` namespace.

Base IRIs:

- `amol:` = `https://www.fantasymaps.org/amol-ontorag/id/`, this dataset's namespace
  (since v0.5.0; previously `https://ontorag.dev/amol/`, which never resolved);
- `rpg:` = `http://www.rpg-schema.org/1.0/`, the canonical rpg-schema namespace
  (previously `https://rpg-schema.org/ns/rpg#`).

Places are typed `schema:Place`, since rpg-schema has no place class. To move the
dataset to another namespace, rewrite `prefixes.json`, `world.ttl`,
`entities.jsonl`, the chunk `entities` lists and `manifest.ontology.base_iri`
together.

## What the graph contains

- **World** — `amol:MythicEurope` (`rpg:World`)
- **RuleSet** — `amol:ArM5` (`rpg:RuleSet`), publisher Atlas Games
- **Hermetic Arts** — 5 Techniques + 10 Forms (`rpg:Proficiency`, tagged Technique/Form)
- **Houses of Hermes** — all 12 (`rpg:Faction`, tagged Hermetic House)
- **Realms of Power** — Magic, Faerie, Divine, Infernal (`rpg:Tag`)
- **Tribunals** — a selection (`rpg:Faction`, tagged Tribunal)

## entity record shape

See [`entity.schema.json`](https://ontorag.org/vocab/dataset/0.1/entity.schema.json):

```json
{"iri":"https://www.fantasymaps.org/amol-ontorag/id/HouseTremere","types":["http://www.rpg-schema.org/1.0/Faction"],
 "label":"House Tremere","aliases":["House Tremere","Tremere"],
 "summary":"A disciplined, hierarchical House … masters of certamen …","tags":["Hermetic House"],
 "attestedIn":["covenants","definitive-edition-core-rules","houses-of-hermes-true-lineages", "…"],
 "definedIn":["art-academe","city-guild","covenants"]}
```

## Entity ↔ content linking

`build.py` derives an alias dictionary from each entity's `label`/`aliases`.
Multi-word aliases match case-insensitively; single-word aliases require exact
case (so the Art **Animal** isn't confused with the common word *animal*). Each
chunk records the IRIs it mentions in `chunk.entities[]`, which the retriever uses
for graph expansion and for injecting structured facts into the LLM context.

## Loading into Oxigraph (optional)

```bash
# load the graph and run SPARQL
oxigraph load --location ./oxidb --file ontology/world.ttl
oxigraph query --location ./oxidb \
  --query 'PREFIX rpg:<http://www.rpg-schema.org/1.0/> SELECT ?h WHERE { ?h a rpg:Faction }'
```

## Regenerating `entities.jsonl`

It is rebuilt from `world.ttl` on every `build.py` run. Edit `world.ttl` (the
source of truth), never `entities.jsonl` directly.

## Extraction history

Entities are extracted by Claude agents from the chunk corpus
(`tools/make_batches.py` → `tools/extract_ontology.workflow.js`, 163 batches of
up to 120 chunks), then deduplicated without an LLM (`tools/recover_extract.py`).

- **June 2026 (v0.4.1–0.5.0):** the run stopped after 66 of 163 batches, so only 8
  books had entities extracted from their own text; the other 17 were linked only to
  entities of those 8. Kept in `_extract/june/recovered.json`.
- **September 2026 (v0.6.0):** the remaining 97 batches (10,393 chunks, 17 books)
  were extracted (`_extract/raw2/`, one file per batch) and merged with
  `tools/merge_extract.py`. Entities already published keep their name and IRI; two
  published entities are never merged into one. Result: 13,014 entities, every book
  with entities of its own (at least 60 unique to each book).

