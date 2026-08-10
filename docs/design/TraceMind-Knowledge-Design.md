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
