# Composition model (packs)

This dataset is **composable**. The ontology and content decompose into a shared
**spine** plus one **pack** per sourcebook. You can add or remove packs to rebuild a
scoped world. The model and its rules are defined by the OntoRAG Provenance and
Citation Ontology: <https://ontorag.org/provenance/#packs>. This page describes
how this dataset implements them.

## Definitions

- **Pack**: one sourcebook, registered in
  [`../content/books.json`](../content/books.json) as `{title, path, requires:[…]}`.
  A pack owns the entities, chunks and embedding vectors attributed to its book.
  Chunks and vectors are stored **one file per pack**
  (`content/chunks/<pack>.jsonl`, `embeddings/vectors/<pack>.jsonl`), so a pack is
  a concrete slice of the dataset. In the graph each book is an `orp:Source` with
  its `orp:SourceFile` (checksum) and an `orp:Pack` (`orp:packOf`, `orp:namedGraph`).
- **Dependency**: `orp:requires` between packs forms a DAG, mirrored by
  `dcterms:requires` between their sources. Every supplement requires the core
  rules, and the **core** is the set of roots (packs that require nothing), here
  `definitive-edition-core-rules`.
- **Spine**: the always-present shared vocabulary (the `orp:SpinePack`). It holds
  the entities with an empty `definedIn`: the hand-curated World, RuleSet, Arts and
  grouping tags. The spine is in scope in every selection.
- **Attestation**: `orp:attestedIn` (books whose prose mentions an entity) and
  `orp:definedIn` (the book it was extracted from), also kept as slug arrays in
  `ontology/entities.jsonl`.

## Selecting packs

Given a set of packs `S`:

- **chunk / vector in scope** ⟺ its book ∈ `S`.
- **entity in scope** ⟺ `attestedIn ∩ S ≠ ∅`, **or** it is a spine entity.

How `S` is built depends on the purpose:

- **Access scope**: `S` is exactly the packs a consumer holds. It is **not**
  closed over `orp:requires`, because holding a supplement does not grant the core
  book it builds on.
- **Composition**: `S` is the selected packs closed over `orp:requires`, which gives
  a coherent world for building datasets, per-pack evaluation (e.g.
  needle-in-a-haystack scoped to a subset) and shipping or retiring a source
  without rebuilding the rest.

## Tooling

[`../tools/compose.py`](../tools/compose.py):

```bash
compose.py --validate                       # integrity + soundness of the dataset
compose.py --packs covenants                # composition: orp:requires pulls in the core
compose.py --packs covenants --no-closure   # access scope: only the packs held
compose.py --packs covenants,mystery-cults  # multiple packs
compose.py --json view.json                 # write the composed view manifest
```

`--validate` checks that:

- `requires` targets exist and form a DAG;
- every `attestedIn`/`definedIn` slug is a known pack;
- there are no dangling chunk→entity references;
- every pack has a chunk file.

A soft warning flags entities *defined* in a pack that does not *attest* them. This
happens when extraction found a name that the alias-linker didn't re-match in prose.

## Manifest

`manifest.json` carries a `composition` block describing the model, the pack
registry, the dependency predicate (`orp:requires`) and the core roots. It follows
the OntoRAG dataset format: <https://ontorag.org/vocab/#format>.

## Not yet (roadmap)

- **Per-pack named graphs**: `world.ttl` is the union of all packs. Each pack
  declares the named graph its statements belong in, but the file is not yet split
  into one graph per pack.
- **Cross-pack entity identity**: a shared IRI/core registry, so that the same
  entity in two packs resolves to one node. This is needed before packs can move
  into separate repositories.
