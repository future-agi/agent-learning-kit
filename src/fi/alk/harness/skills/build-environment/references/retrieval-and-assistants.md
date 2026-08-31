---
name: retrieval-and-assistants
description: "The agent answers from a corpus. Evidence: a vector store client (pgvector, pinecone, weaviate, chroma, qdrant), an embedding model, a rerank or retrieval service, or an assistant API whose answer quality depends on retrieved documents. The world is the corpus, its index and the real retrieval service. NOT this file when retrieval is incidental and the graded behaviour is a transaction."
---

# RAG systems and assistants reached over an API

> **Selection check.** You are in the right file if what the agent is graded on is whether it retrieved and used the right material. If it is graded on completing a transaction, you are in the wrong file.

The world is a corpus, its index and the retrieval service the agent actually calls. Build all
three from the submitted repository when it provides them; do not substitute a hand-written search
endpoint for an unavailable implementation.

## Establish the retrieval contract

Read the ingestion job, chunking settings, embedding configuration, index name or namespace,
metadata filters and query client. Determine whether indexing is synchronous, queued or eventually
consistent, and identify the documented configuration seam for an isolated index. Preserve the
agent's retrieval settings. Changing chunk boundaries, ranking configuration or metadata rules can
make a correct-looking answer come from a different retrieval system.

## Seed a corpus that can disprove the agent

Include sources with knowably supported answers, deliberately absent answers, near matches that
should be excluded, and records that require metadata filtering or access control. Keep source
documents and metadata traceable so a check can state why an answer is or is not supported. Do not
seed only the happy-path document; an assistant that answers confidently from an empty or unrelated
index is one of the defects this environment must expose.

## Verify retrieval and answer separately

Record the retrieved chunks, source identifiers, ranking or scores when available, and the final
answer. A check that sees only fluent output cannot distinguish grounded retrieval from a lucky
guess. Check that relevant sources can be retrieved, excluded sources remain excluded, index reset
removes scenario changes, and failures from the shipped service stay visible rather than becoming
empty successful results.

If the repository has no retrieval implementation or no way to redirect the agent to an isolated
index, report the missing seam. Do not manufacture a generic vector service and call it equivalent.