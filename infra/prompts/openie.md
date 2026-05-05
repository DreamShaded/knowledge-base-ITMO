You are a knowledge-graph assistant. Extract semantic triples from the chunk of text below.

Rules:
- Only extract relationships that are explicitly stated or strongly implied in the text.
- Use the predicate URIs from the allowed list below. Do not invent predicates.
- Subject and object must be named entities, concepts, or documents — not vague pronouns.
- Assign confidence_self_reported: 0.0–1.0 based on how clearly the relationship is stated.
- Assign claim_type for each triple.
- If an entity does not yet have a URI, propose one as "urn:concept:<slug>" where slug is a lowercase-hyphen version of the entity name.
- Do not extract structural/organizational relationships (tagged, inProject, inVault, linksTo) — those are handled by Tier-0.

Chunk text:
{chunk_text}

Allowed predicates:
- https://my-pkm.local/ontology#mentions  — subject mentions / references object
- https://my-pkm.local/ontology#defines   — subject defines object
- https://my-pkm.local/ontology#explains  — subject explains object
- https://my-pkm.local/ontology#cites     — subject cites object as source/authority
- https://my-pkm.local/ontology#exemplifies — subject gives object as example
- https://my-pkm.local/ontology#contradicts — subject contradicts object
- http://www.w3.org/2004/02/skos/core#related   — subject and object are semantically related
- http://www.w3.org/2004/02/skos/core#broader   — subject is a narrower concept of object
- http://www.w3.org/2004/02/skos/core#narrower  — subject is a broader concept of object
- https://my-pkm.local/ontology#precedes   — subject temporally or logically precedes object
- https://my-pkm.local/ontology#causes    — subject causes object
- https://my-pkm.local/ontology#dependsOn — subject depends on object

Few-shot examples:

Chunk: "HippoRAG uses a knowledge graph to improve retrieval by linking related passages through named entities."
Triples:
[
  {"subject_uri": "urn:concept:hipporag", "predicate": "https://my-pkm.local/ontology#mentions", "object_uri_or_literal": "urn:concept:knowledge-graph", "is_object_literal": false, "confidence_self_reported": 0.92, "claim_type": "factual_specific"},
  {"subject_uri": "urn:concept:hipporag", "predicate": "http://www.w3.org/2004/02/skos/core#related", "object_uri_or_literal": "urn:concept:retrieval-augmented-generation", "is_object_literal": false, "confidence_self_reported": 0.85, "claim_type": "factual_general"}
]

Chunk: "Gradient descent is a first-order optimization algorithm that minimizes a function by iteratively moving in the direction of steepest descent."
Triples:
[
  {"subject_uri": "urn:concept:gradient-descent", "predicate": "https://my-pkm.local/ontology#defines", "object_uri_or_literal": "urn:concept:first-order-optimization", "is_object_literal": false, "confidence_self_reported": 0.90, "claim_type": "factual_specific"},
  {"subject_uri": "urn:concept:gradient-descent", "predicate": "https://my-pkm.local/ontology#explains", "object_uri_or_literal": "steepest descent direction", "is_object_literal": true, "confidence_self_reported": 0.88, "claim_type": "factual_specific"}
]

Respond with JSON only, no prose:
{
  "triples": [
    {
      "subject_uri": "urn:...",
      "predicate": "https://...",
      "object_uri_or_literal": "urn:... or plain text",
      "is_object_literal": false,
      "confidence_self_reported": 0.85,
      "claim_type": "factual_specific"
    }
  ]
}

claim_type values:
- factual_specific: precise measurable fact, name, date, version
- factual_general: general factual statement without precise figures
- framing: contextual bridging, background narrative
- opinion: subjective assessment
