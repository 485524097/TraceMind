# TraceMind Knowledge Design

## Stage 13 — Problem & Solution Knowledge

### Problem

Conversation answers are useful but remain chronological and difficult to maintain as durable
engineering knowledge. Stage 13 adds an explicit conversion from one completed assistant answer to
one structured knowledge entry without changing the RAG pipeline.

### Constraints

- Knowledge creation starts from a completed assistant message in the same knowledge base.
- The client may edit the structured fields but cannot submit provenance or evidence snapshots.
- Deleting a conversation must not delete the durable knowledge or its snapshots.
- Tags and evidence are values on the entry, not separate domain entities.
- The implementation adds one business table and one transaction boundary per mutation.

### Data and provenance contract

`knowledge_entries` stores the editable question, background, root cause, solution, failed
attempts, validation status and normalized tags. It also stores immutable snapshots of the paired
user question, assistant answer, cited sources and safe generation metadata.

The service resolves the conversation and paired user message from the submitted assistant message
ID. It filters the answer's actual `[Sx]` citations against `ConversationMessage.sources`, validates
them as `RagSource`, checks their knowledge-base scope and copies only display-safe source fields.
Retrieval scores, ranks, index generations, retrieval queries and prompts are not persisted.

Source foreign keys use `ON DELETE SET NULL`; snapshots remain available when their original
conversation is deleted. A unique constraint on the assistant message enforces one current entry
per answer. A knowledge base containing documents or knowledge entries cannot be deleted.

### Alternatives not adopted

- Tag and source tables were rejected because tags are simple filter values and evidence is an
  immutable snapshot, not independently managed data.
- Manual standalone entry creation was deferred so provenance remains deterministic in the MVP.
- Full-text search infrastructure was deferred; bounded case-insensitive SQL search is sufficient
  for the local-first MVP.

### Validation

Unit and API tests cover provenance resolution, source allowlisting, cross-KB rejection, duplicate
answers, CRUD, filters and rollback. PostgreSQL integration tests cover migration round trips and
the `SET NULL` snapshot-preservation behavior. Frontend tests cover the save workflow, resource
list and shared Evidence renderer.

### Current limits

- Tags are normalized with Unicode `casefold()` and stored in lowercase-like canonical form.
- Search is SQL substring matching rather than a ranked full-text index.
- Knowledge entries are created only from persisted completed answers.

## Stage 14 — Derived Knowledge Map

The Knowledge Map is a read-only projection, not a retrieval or storage subsystem. The scoped map
endpoint loads the current Knowledge Base, live Documents and KnowledgeEntries, then derives four
node types and four transparent edge types at request time.

- `contains`: the Knowledge Base contains each live Document and KnowledgeEntry.
- `cites`: a KnowledgeEntry snapshot names a Document that still exists in the same Knowledge Base.
- `tagged`: a KnowledgeEntry contains a normalized tag value; `tag:{value}` is its stable node ID.
- `related`: two entries share a tag or a live cited Document. One stable undirected edge aggregates
  all matching tag and document reasons.

Deleted-document snapshots remain visible in Knowledge detail but intentionally produce no live
Document node, cite edge or document-based related reason. The map adds no model, table, migration,
cache, graph database, entity extraction or GraphRAG path. Its current in-memory derivation is an
MVP trade-off for local knowledge-base sizes; large-graph pagination or clustering is deferred.

## Stage 16 — Verified Knowledge Retrieval Loop

### Problem

Stage 13 made completed answers maintainable, but saved entries remained browse-only. Later RAG
requests could retrieve only original document chunks, so verified problem-solving experience did
not return to the answer loop.

### Adopted design

- Only `verified` KnowledgeEntry content participates in default RAG. `unverified` and `outdated`
  entries are excluded by database-owned active-generation selection, even if stale Qdrant points
  still await cleanup.
- Maintained fields are indexed: question, background, root cause, solution, failed attempts and
  tags. Immutable assistant answer snapshots are deliberately excluded to avoid reinforcing an
  unverified generated answer.
- KnowledgeEntry uses the existing Embedding provider and Qdrant collection, but has an explicit
  `source_type=knowledge_entry`, its own entry ID, chunks, active/attempt generations and indexing
  status. It is never represented as a synthetic Document.
- RAG embeds the query once and runs one Dense/BM25/RRF search over the union of active document
  and verified-knowledge generations. Explicit document/path or language scope remains
  document-only.
- Knowledge sources keep the existing `[Sx]` citation identity, open the maintained entry, and
  identify themselves as verified knowledge. Saving an answer that cites maintained knowledge
  snapshots the source identity without recursively copying its provenance graph.
- Create/update/status changes enqueue an idempotent Celery sync. Updating maintained fields makes
  an older generation ineligible immediately. Moving away from `verified` removes the active
  generation; deletion schedules entry-scoped point cleanup.

### Alternatives not adopted

- Indexing `answer_snapshot` directly was rejected because generated text is evidence only after a
  user maintains and verifies it.
- A second Qdrant collection was rejected because it would require a second query embedding or
  application-side cross-collection score fusion without providing an MVP trust benefit.
- Synchronous indexing in the mutation request was rejected because the local embedding model can
  make ordinary knowledge editing block for an unpredictable duration.
- Treating entries as Markdown Documents was rejected because it would create fake file identity
  and weaken citation semantics.

### Migration, recovery and limits

Migration `20260814_0011` adds indexing state only; existing entries remain `not_indexed`. A user
must mark an entry verified, edit an already verified entry, or explicitly retry indexing before it
is retrieved. Repeated sync is generation-safe: only the current attempt may activate, old or
failed generations are deleted, and an unavailable queue leaves a visible retryable error.

Qdrant remains derived data. Existing document points without `source_type` continue to deserialize
as documents. `ensure_collection` adds the new keyword payload indexes without requiring a document
reindex. Orphan cleanup after a permanently unavailable Qdrant/queue is still a broader maintenance
concern and belongs to the planned consistency-audit stage.
