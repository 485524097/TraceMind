# TraceMind UI Design Specification

> Single source of truth for TraceMind's visual language.
> Read before modifying any frontend presentation.

## Product Identity

TraceMind is a **local-first, traceable personal knowledge workspace for developers.**

It is **not**:
- an admin dashboard
- a CRUD management system
- a generic SaaS dashboard
- a marketing landing page
- a ChatGPT clone
- an IDE clone
- a Coding Agent

## Design Direction

**TraceMind — Minimal Technical Workspace**

Core qualities: Minimal · Precise · Technical · Calm · Traceable

Core principle: Minimal clarity + Developer precision + Inspectable evidence

---

## Information Hierarchy

### L1 — Primary Content (dominates visually)
- User question
- AI answer
- Documents
- Knowledge Bases
- Search results
- **Sources / Citations / Evidence** (first-class product capability)

### L2 — Context Metadata (supports L1)
- File path
- Section / Page / Chunk
- Version
- Method signature
- Line range
- Status tags

### L3 — Execution / Debug (discoverable but secondary)
- Query rewrite mode
- Retrieval mode (hybrid, reranker)
- Reranker fallback
- Latency
- Symbol scope / Path scope
- Trace metadata

**Rules:**
- L1 dominates. L2 supports L1. L3 remains discoverable but visually secondary.
- Sources / Evidence are **always L1**. Never demote them to metadata.
- L3 must use progressive disclosure (collapsed by default).

---

## Global Shell

Two semantic layers. No global sidebar.

### Global Bar (44px)
```
TraceMind                              Knowledge Bases
```
- Brand wordmark on left. No decorative logo.
- Knowledge Bases link on right. Active state = bottom border accent.
- `1px solid` bottom border. No shadow. No gradient.

### KB Context Bar (38–40px, only when inside a KB)
```
Current KB Name                       Documents    Ask
```
- KB name from page data (existing `knowledgeBaseName` ref).
- Documents / Ask as text tabs. Active state = bottom border accent.
- No global KB selector dropdown at current stage.
- No duplicated Documents/Ask controls.

**Implementation:** `AppShell.vue` with `provide/inject` for KB name.

---

## Home

Minimal product start screen. No marketing treatment.

Structure:
- "TraceMind" heading + product description
- "Open Knowledge Bases →" CTA (primary button)
- Recent KBs section (if backend returns data)
- Compact backend status (only when unavailable)

**Anti-patterns to avoid:**
- Marketing hero cards
- Large shadows
- Gradients
- Backend status as dominant content
- Feature lists

**File:** `src/views/HomeView.vue`

---

## Knowledge Bases

Knowledge Bases are workspaces, not database rows.

### Visual Pattern: Editorial Resource Row
- Each KB is a full-width clickable row
- Name + description + updated date
- Whole-row navigation to documents
- Contextual actions (Edit, Delete) in `···` overflow dropdown
- "New" as the primary page action

**Anti-patterns to avoid:**
- CRUD tables with action columns
- Equal-weight inline buttons

**File:** `src/views/KnowledgeBaseView.vue`

---

## Documents

Documents are knowledge sources for search, answers, and citations.

### Page Structure
```
Page Header: "Documents" + description + [Import]
Search: [Filter by name or path…]
Document List: editorial resource rows
Retrieval Tools: collapsible at bottom
```

### Visual Pattern: Editorial Resource Row
Each document row shows:
- **Filename** (without extension) + **extension** in mono
- **Relative path** in mono, secondary color
- **Metadata row**: version · size · chunks · date
- **Status pills**: parsed/indexed with colored dot indicators
- **Overflow** `···`: Chunks, Re-parse, Re-index, Download, Versions, Delete

### Import
- Compact "Import" button in page header
- Opens existing `DocumentUploadPanel` inline (collapsible)
- Preserves file import + code directory import

### Retrieval Tools
- "▸ Retrieval tools" toggle at page bottom
- Expands `SemanticSearchPanel` (Dense/Hybrid/Reranker)
- Collapsed by default

**Anti-patterns to avoid:**
- CRUD tables
- Permanent upload panel
- Action button rows

**File:** `src/views/DocumentView.vue`

---

## Ask / Conversation

Core product page. Three-area layout at desktop (1440px).

### Layout (approximate proportions)
```
Conversations (200px) | Answer (flex) | Evidence (360px)
```

### Conversation History (left)
- "Conversations" header
- Compact list: title + relative date
- Selected state: accent background + left border
- "+ New" button at bottom
- Rename/Delete in `···` overflow (not permanent buttons)

### Answer (center)
- **No ChatGPT-style bubbles.**
- User messages: "You" label + left-border content area
- Assistant messages: "TraceMind" label + reading-body text
- Inline citation pills: `[S1]` — blue accent, monospace, clickable
- **Provenance row** below each answer: "Cited from N sources"
- **No duplicate full sources below the answer.** Evidence lives in the Inspector.
- Execution details: collapsed `▸` summary (L3)

### Evidence Inspector (right)
- **Visible by default** at desktop width
- Collapsible via `×` button; when collapsed, Answer expands
- Clicking a citation `[S1]` re-opens the inspector
- Two sections: **Sources** (L1) + **Execution** (L3)

### Source/Evidence Types

**Document Evidence:**
```
# DOCUMENT
[S1]  filename.md
§ Section · Chunk N
excerpt…
```

**Code Evidence:**
```
<> CODE
[S3]  ClassName.java
src/path/to/ClassName.java
public ReturnType methodName(Params)
L42–58
code excerpt…
```

- Source type distinguished by `# DOCUMENT` / `<> CODE` labels + composition
- Not color alone
- Code evidence: method signature + line range + code block with left accent border

**Anti-patterns to avoid:**
- ChatGPT-style chat bubbles
- Duplicated evidence (inline + inspector)
- Hiding sources behind `<details>`
- "GROUNDED" claims

**File:** `src/views/ConversationView.vue`

---

## Citation System

- One consistent citation identity: `[S1]`, `[S2]`, `[S3]`
- Blue-accent pill with monospace font
- Same color for all citations (document and code)
- Source TYPE distinguished in Evidence Inspector via labels, not citation color
- Clicking a citation opens/focuses the Evidence Inspector

## Problem & Solution Knowledge

Knowledge entries are durable engineering records saved from completed answers.

- The Knowledge list uses editorial resource rows, not a CRUD table or card grid.
- Search, validation status and tag filters remain compact and secondary to the entries.
- A detail page gives the solution primary reading space and keeps Evidence visible as L1 content.
- Background, root cause and failed attempts appear only when present.
- The original conversation is linked when it still exists; immutable question, answer and source
  snapshots remain visible after it is deleted.
- Editing changes the maintained knowledge fields, never the provenance snapshots.

---

## Visual Language

### Color Roles
| Role | Usage |
|------|-------|
| Background (`--color-bg`) | Page background, warm near-white |
| Surface (`--color-surface`) | Cards, panels, inspector |
| Text (`--color-text`) | Primary content |
| Text secondary (`--color-text-secondary`) | Metadata, labels |
| Text tertiary (`--color-text-tertiary`) | Captions, timestamps |
| Accent (`--color-accent`) | Navigation active, citations, focus, links, primary buttons |
| Border (`--color-border`) | Hairline separators |
| Success/Warning/Error | Semantic states only |

Blue is used ONLY for: navigation active, citations, focus, primary actions, links.
Green = success. Not for code evidence identity.

### Typography
- **System font stack** (no external CDN): `system-ui, 'PingFang SC', 'Microsoft YaHei UI', 'Segoe UI'`
- **Mono stack**: `'Cascadia Code', 'JetBrains Mono', Consolas, monospace`
- **Scale**: 24px (page titles) > 15px (reading body) > 14px (UI) > 13px (metadata) > 11px (micro)
- No giant titles. No decorative eyebrows. No excessive uppercase.

### Separators
- Hairline `1px solid` borders
- `border-bottom` on resource rows
- No heavy borders, no card wrappers around everything

### Surfaces
- White only when a surface is actually needed
- No card-ification of every section
- No shadows (or minimal `0 1px 3px` for dropdowns)
- No gradients, no glass

### Buttons
- Primary: accent background, white text
- Secondary: border + transparent background
- Text: no border, no background
- Overflow actions: `···` trigger → Element Plus Dropdown

### Metadata
- Mono font, compact, secondary/tertiary color
- Status pills: colored dot + label, small size
- Timestamps: relative where practical

---

## Element Plus Integration

Element Plus is an **implementation dependency**, not TraceMind's visual identity.

**Use Element Plus for:** Dialog, Dropdown, Input behavior, Button behavior, Loading, Message, Confirmation.

**Override:** Primary color → `--color-accent`. Border radius → project tokens. Font family → project tokens.

**Do not:** introduce a second component framework (no Tailwind, no shadcn).

---

## New Feature Rules

For every new frontend feature:
1. Determine L1 / L2 / L3 classification for new information.
2. Inspect analogous existing TraceMind UI first.
3. Reuse an existing design pattern (resource row, provenance row, evidence item, etc.).
4. Preserve global shell/navigation semantics.
5. If introducing a genuinely reusable new UI pattern, update this document.
6. Never create a page-specific visual language silently.

---

## Review Checklist

Before completing any frontend UI work:
- [ ] L1/L2/L3 classification is correct
- [ ] Evidence/Sources remain L1, not buried or duplicated
- [ ] Execution/debug uses progressive disclosure
- [ ] No CRUD tables for knowledge objects
- [ ] No card-heavy SaaS layouts
- [ ] No ChatGPT-style bubbles
- [ ] No duplicated navigation
- [ ] Shell layers preserved
- [ ] Element Plus visual defaults overridden where needed
- [ ] `vue-tsc --noEmit` passes
- [ ] `eslint` passes
- [ ] `vitest` passes
- [ ] `vite build` passes
