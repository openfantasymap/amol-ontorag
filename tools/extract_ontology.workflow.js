export const meta = {
  name: 'extract-amol-ontology',
  description: 'Extract & describe the Ars Magica ontology from the chunk corpus with Claude agents',
  phases: [
    { title: 'Extract', detail: 'one agent per chunk batch -> typed, described entities' },
    { title: 'Describe', detail: 'synthesize one authoritative description per unique entity, write shards' },
  ],
}

// Paths are hardcoded (a prior run showed `args` does not reliably bind inside the
// workflow). Override via args only if explicitly provided.
const ROOT = '/srv/ofm/amol-ontorag'
const batchesPath = (args && args.batchesPath) || `${ROOT}/ontology/_extract/batches.json`
const outDir = (args && args.outDir) || `${ROOT}/ontology/_extract`
const descDir = `${outDir}/desc`

const BATCHES_SCHEMA = {
  type: 'object', required: ['batches'], additionalProperties: false,
  properties: {
    batches: {
      type: 'array',
      items: {
        type: 'object', required: ['batch_id', 'path', 'n'], additionalProperties: false,
        properties: { batch_id: { type: 'string' }, path: { type: 'string' }, n: { type: 'integer' } },
      },
    },
  },
}

const TYPES = 'Character, House, Tribunal, Covenant, Faction, Spell, Item, Creature, Virtue, Flaw, Ability, Art, Concept, Realm, Place, CharacterType'

const EXTRACT_SCHEMA = {
  type: 'object', required: ['entities'], additionalProperties: false,
  properties: {
    entities: {
      type: 'array',
      items: {
        type: 'object', required: ['name', 'type'], additionalProperties: false,
        properties: {
          name: { type: 'string' },
          type: { type: 'string' },
          aliases: { type: 'array', items: { type: 'string' } },
          description: { type: 'string' },
          evidence: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const WRITE_SCHEMA = {
  type: 'object', required: ['written', 'path'], additionalProperties: false,
  properties: { written: { type: 'integer' }, path: { type: 'string' } },
}

const chunk = (arr, n) => { const o = []; for (let i = 0; i < arr.length; i += n) o.push(arr.slice(i, i + n)); return o }
const norm = (s) => s.toLowerCase().replace(/^the\s+/, '').replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim()

// ---------------------------------------------------------------- Load batch index
phase('Extract')
const idx = await agent(
  `Read the JSON file at ${batchesPath} using the Read tool and return its parsed contents verbatim. It is an array of objects {batch_id, path, n}. Return {"batches": <that array>}.`,
  { label: 'load-batches', phase: 'Extract', schema: BATCHES_SCHEMA }
)
const batches = idx.batches
log(`loaded ${batches.length} batches`)

// ---------------------------------------------------------------- Extract
const extractPrompt = (b) => `You are extracting a knowledge-graph ontology from the tabletop RPG *Ars Magica* (the Mythic Europe setting).

Read the file: ${b.path}
It has ${b.n} JSON lines, each {id, heading_path, text} drawn from the source books.

Extract EVERY distinct NAMED or game-DEFINED entity that appears, exhaustively. For each:
- name: canonical name (e.g. "Pilum of Fire", "House Flambeau", "Parma Magica", "Covenant of Harco", "The Gift").
- type: exactly ONE of: ${TYPES}.
- aliases: other short surface forms used in the text (omit if none).
- description: 1-2 sentences, grounded ONLY in this text.
- evidence: up to 3 of the chunk "id" values where it appears.

DO extract: spells; magic items/enchanted devices; characters/magi/NPCs/founders; Houses; Tribunals; covenants; factions/cults/orders/lineages; creatures/monsters/beasts; Virtues; Flaws; Abilities/skills; the 15 Hermetic Arts (Techniques & Forms); Hermetic concepts & rules (e.g. Twilight, certamen, vis, the Code of Hermes, Aura); Realms of Power; named places/regions.
DO NOT extract: generic common nouns, rules math, page furniture, table-of-contents lines, author/playtester/credit names, or anything not a real in-world or game entity.

Return JSON matching the schema. If the batch yields nothing extractable, return {"entities": []}.`

const extracted = await parallel(
  batches.map((b) => () =>
    agent(extractPrompt(b), { label: `extract:${b.batch_id}`, phase: 'Extract', schema: EXTRACT_SCHEMA })
  )
)

// ---------------------------------------------------------------- Aggregate (in-script)
const mentions = extracted.filter(Boolean).flatMap((r) => r.entities || [])
log(`extracted ${mentions.length} raw mentions from ${batches.length} batches`)

const map = new Map()
for (const m of mentions) {
  if (!m || !m.name) continue
  if (!/[a-zA-Z]/.test(m.name)) continue
  const k = norm(m.name)
  if (k.length < 2) continue
  let e = map.get(k)
  if (!e) { e = { name: m.name, types: {}, aliases: new Set(), descs: [], evidence: new Set(), count: 0 }; map.set(k, e) }
  e.count++
  if (m.type) e.types[m.type] = (e.types[m.type] || 0) + 1
  ;(m.aliases || []).forEach((a) => { if (a && a.length <= 60) e.aliases.add(a) })
  e.aliases.add(m.name)
  if (m.description) e.descs.push(m.description)
  ;(m.evidence || []).forEach((x) => e.evidence.add(x))
}

let uniques = [...map.values()].map((e) => ({
  name: e.name,
  type: Object.entries(e.types).sort((a, b) => b[1] - a[1])[0]?.[0] || 'Concept',
  aliases: [...e.aliases].slice(0, 12),
  candidate_descriptions: e.descs.slice(0, 6),
  mentions: e.count,
  evidence: [...e.evidence].slice(0, 8),
}))
uniques.sort((a, b) => b.mentions - a.mentions || a.name.localeCompare(b.name))
log(`aggregated to ${uniques.length} unique entities`)

// ---------------------------------------------------------------- Describe (write shards)
phase('Describe')
const groups = chunk(uniques, 25)
const described = await parallel(groups.map((grp, gi) => () => {
  const shard = `${descDir}/shard_${String(gi).padStart(3, '0')}.json`
  const payload = JSON.stringify(grp.map((e) => ({
    name: e.name, type: e.type, aliases: e.aliases, mentions: e.mentions,
    candidate_descriptions: e.candidate_descriptions, evidence: e.evidence,
  })))
  const p = `You are finalizing entities for an *Ars Magica* knowledge-graph ontology.

Here are ${grp.length} candidate entities, each with descriptions gathered from the source text:
${payload}

For EACH entity produce a finalized record with keys exactly: name, type, aliases, description, evidence.
- name: cleaned canonical name.
- type: confirm or correct to exactly ONE of: ${TYPES}.
- aliases: deduplicated useful surface forms (drop near-duplicates; you may drop the name itself).
- description: ONE authoritative, self-contained description (1-3 sentences) synthesized from the candidates — no contradictions, no meta-references like "the text says". If candidates are thin, write the best accurate description you can from Ars Magica domain knowledge.
- evidence: keep the provided chunk ids (up to 5).

Then WRITE all ${grp.length} finalized records as a single JSON ARRAY to this exact path using the Write tool:
${shard}

After writing, return JSON {"written": <number of records>, "path": "${shard}"}.`
  return agent(p, { label: `describe:${gi}`, phase: 'Describe', schema: WRITE_SCHEMA })
}))

const shardsOk = described.filter(Boolean)
const totalWritten = shardsOk.reduce((s, r) => s + (r.written || 0), 0)

return {
  batches: batches.length,
  raw_mentions: mentions.length,
  unique_entities: uniques.length,
  describe_groups: groups.length,
  shards_written: shardsOk.length,
  records_written: totalWritten,
  shardDir: descDir,
}
