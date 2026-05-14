# Future Work & Research Backlog

Items that are out of scope for the current implementation but worth tracking.
Not ordered by priority. Add to this document freely during development when
ideas arise that shouldn't block current work.

---

## UX — User-directed consolidation

**Type:** Feature  
**Depends on:** Ticket 4 (consolidate.py + priority queue)

When a UI exists, the user should be able to influence the consolidation priority
queue directly rather than relying entirely on the algorithmic scoring.

**The idea:**
The user is reading two wiki pages and suspects they are related in a way the
graph hasn't captured yet. A "consolidate these" button bumps both nodes to the
top of the priority queue and triggers an immediate consolidation run for those
nodes specifically.

**Why this matters:**
The algorithmic priority queue is driven by graph structure — degree, staleness,
bridge factor. But the human reader has semantic insight the algorithm lacks. They
may notice a connection between two low-degree nodes that the graph scores as
low priority. User-directed consolidation is the escape hatch for cases where
human intuition outruns the algorithm.

**Implementation sketch:**
- Priority queue accepts optional `pinned` node keys that sort above all scored nodes
- `consolidate.py --pin concepts/shadow.md --pin concepts/impermanence.md` runs
  immediately for those two nodes regardless of their algorithmic score
- The UI calls this via a subprocess or a small local API wrapper
- Pinned consolidations are logged distinctly in `wiki/log.md` so the human can
  see which connections they initiated vs which emerged algorithmically

---

## Research Spike — Hallucination drift over consolidation cycles

**Type:** Research / Investigation  
**Depends on:** Ticket 6 (consolidation phase working end-to-end)

**The problem:**
The LLM treats the wiki as source of truth when consolidating. Each consolidation
pass reads existing wiki pages and rewrites them. If the LLM introduces a subtle
inaccuracy — a hallucinated connection, a misattributed claim, a concept slightly
distorted — that inaccuracy becomes part of the wiki and is read as fact on the
next consolidation pass. Over many cycles, errors compound.

The wiki could gradually drift from the original source documents into something
that looks authoritative but bears little resemblance to what the sources actually
said. An extraordinary work of fiction with good footnotes.

**The experiment:**
1. Create a minimal corpus — 3 or 4 short documents with specific, verifiable claims
2. Run ingest to produce the initial wiki
3. Run consolidation repeatedly — 10, 20, 50 cycles — well beyond what would be
   useful in practice
4. At each checkpoint, measure divergence between wiki claims and source document
   claims using a separate LLM evaluation call as judge
5. Plot divergence against consolidation cycle count
6. Identify at what cycle count the wiki becomes unreliable

**Questions to answer:**
- Is drift linear, exponential, or does it plateau?
- Are certain node types more susceptible? (analyses pages vs concept pages)
- Do bridge nodes drift more because they're consolidated most frequently?
- Does drift correlate with consolidation_version?

**Potential mitigations to evaluate:**
- Source anchoring — always include the original source page content in
  consolidation context, not just the current wiki page. More expensive but
  keeps the LLM grounded in primary sources.
- Claim provenance — require the LLM to tag every claim with its source.
  Untagged claims in a consolidated page are flagged as potentially hallucinated.
- Consolidation depth limit — cap `consolidation_version` at a configurable
  maximum. Pages that have been consolidated N times are frozen until new source
  material arrives. Forces the wiki to stay grounded.
- Periodic ground-truth audit — a separate lint pass that spot-checks wiki claims
  against original source documents and flags divergence for human review.

**Why this matters:**
If drift is significant and early, the entire consolidation approach needs
rethinking. If drift is slow and detectable, mitigations can be added. This
experiment should be run before committing to heavy consolidation use on a
corpus you care about.
