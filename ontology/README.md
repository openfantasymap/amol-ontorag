# Ontology layer

The knowledge graph that makes retrieval *graph-aware*.

| File | Role |
|------|------|
| `world.ttl` | **Source of truth.** The domain knowledge graph in Turtle. Hand-authored. Loadable into [Oxigraph](https://github.com/oxigraph/oxigraph), `rdflib`, or any RDF store. |
| `entities.jsonl` | A flat projection of `world.ttl` (one entity per line), **generated** by `tools/build.py`. Lets a consumer load the graph lens and build the chunk-linking dictionary without a SPARQL engine. |
| `prefixes.json` | Namespace prefixes used in `world.ttl`. |

## Schema alignment

Classes/properties prefixed `rpg:` align with the **rpg-schema** TTRPG ontology
(catalog slug `rpg`): `rpg:World`, `rpg:RuleSet`, `rpg:Faction`,
`rpg:Proficiency`, `rpg:Tag`, with `rpg:inWorld`, `rpg:hasTag`,
`rpg:capabilityDefinedInRuleSet`, etc. Standard `schema:`, `dc:`, `foaf:` and
`skos:` vocabularies are used for labels, descriptions and provenance. All
instance data lives under the `amol:` namespace.

The base IRIs (`amol:` = `https://ontorag.dev/amol/`, `rpg:` =
`https://rpg-schema.org/ns/rpg#`) are placeholders aligned to the rpg-schema
catalog; rewrite them to match your deployment if needed (update `prefixes.json`,
`world.ttl`, and `manifest.ontology.base_iri` together).

## What the graph contains

- **World** — `amol:MythicEurope` (`rpg:World`)
- **RuleSet** — `amol:ArM5` (`rpg:RuleSet`), publisher Atlas Games
- **Hermetic Arts** — 5 Techniques + 10 Forms (`rpg:Proficiency`, tagged Technique/Form)
- **Houses of Hermes** — all 12 (`rpg:Faction`, tagged Hermetic House)
- **Realms of Power** — Magic, Faerie, Divine, Infernal (`rpg:Tag`)
- **Tribunals** — a selection (`rpg:Faction`, tagged Tribunal)

## entity record shape

See [`../schema/entity.schema.json`](../schema/entity.schema.json):

```json
{"iri":"https://ontorag.dev/amol/HouseTremere","types":["https://rpg-schema.org/ns/rpg#Faction"],
 "label":"House Tremere","aliases":["House Tremere","Tremere"],
 "summary":"A disciplined, hierarchical House … masters of certamen …","tags":["Hermetic House"]}
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
  --query 'PREFIX rpg:<https://rpg-schema.org/ns/rpg#> SELECT ?h WHERE { ?h a rpg:Faction }'
```

## Regenerating `entities.jsonl`

It is rebuilt from `world.ttl` on every `build.py` run. Edit `world.ttl` (the
source of truth), never `entities.jsonl` directly.
