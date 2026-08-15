# Composition model (packs)

OntoRAG datasets are **composable**: the ontology + content decompose into a
**core** plus modular **packs** that can be added to or removed from that core to
reconstruct a scoped world. This is the formalization of the v0.4.2 book-provenance
layer (`attestedIn` / `definedIn` / `dc:requires`).

## Definitions

- **Pack** — a unit of semantic content. Here 1 pack = 1 sourcebook (registered in
  [`../content/books.json`](../content/books.json)): `{title, path, requires:[…]}`.
  A pack owns the entities, chunks, and embedding vectors attributed to its book.
  Chunks and vectors are stored **one file per pack**
  (`content/chunks/<pack>.jsonl`, `embeddings/vectors/<pack>.jsonl`), so a pack is
  a concrete slice of the dataset — the seam along which packs could later become
  separate repositories.
- **Dependency** — `dc:requires` forms a DAG over packs. Every supplement requires
  the core rules; the **core** is the set of roots (packs that require nothing) —
  here `definitive-edition-core-rules`.
- **Spine** — the always-present, shared semantic vocabulary: entities with an
  empty `definedIn` (the hand-curated World / RuleSet / Arts / grouping tags). The
  spine is in scope for every composition.

## Composition semantics

Given a selected set of packs `P`, close it over `dc:requires` to get the scope
`P*`:

- **chunk / vector in scope** ⟺ its book ∈ `P*`.
- **entity in scope** ⟺ `attestedIn ∩ P* ≠ ∅`, **or** it is a spine entity.

Adding a pack brings its chunks + attested entities into the world; removing a pack
(and, transitively, packs that require it) removes them. This yields a valid
sub-dataset usable for:

- **access-scoping** — a consumer only "knows" the packs it holds;
- **per-pack evaluation** — needle-in-a-haystack scoped to a pack subset;
- **modular knowledge bases** — ship/retire a source without rebuilding the rest.

## Tooling

[`../tools/compose.py`](../tools/compose.py):

```bash
compose.py --validate                       # integrity + soundness of the dataset
compose.py --packs covenants                # scoped view (dc:requires pulls in core)
compose.py --packs covenants,mystery-cults  # multiple packs
compose.py --json view.json                 # write the composed view manifest
```

`--validate` checks: `requires` targets exist and form a DAG; every
`attestedIn`/`definedIn` slug is a known pack; no dangling chunk→entity references;
every pack has a chunk file. Soft warnings flag entities *defined* in a pack that
does not *attest* them (extraction found a name the alias-linker didn't re-match in
prose).

## Manifest

`manifest.json` carries a `composition` block describing the model, the pack
registry, the dependency predicate, and the core roots. See
[`../schema/manifest.schema.json`](../schema/manifest.schema.json).

## Not yet (roadmap)

- **Serve-time scoping** — a `scope=[packs]` filter on retrieval (step 2).
- **Cross-pack entity identity** — a shared IRI/core registry so the same entity in
  two packs resolves to one node; required to graduate packs into separate repos
  (storage-model *b*).
