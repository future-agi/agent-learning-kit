# RAG systems and assistants reached over an API

The world is a corpus and whatever serves it.

What you build: the documents, the index, and the retrieval endpoint the agent calls. Seed the
corpus so that questions have knowably right and knowably absent answers, because an assistant
that answers confidently from an empty index is the failure worth catching.

Checks read what was retrieved and what was answered, so record both. A check that only reads the
answer cannot tell a correct answer from a lucky one.

If the repository ships the retrieval service, run it. Only build your own when there is none.
