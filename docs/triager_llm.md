# triager llm

An Oaken Core triager that asks a model whether an item meets your criteria. AI-generated, unreviewed.

```toml
[extensions]
dirs = ["~/local/oaken-plugins"]

[plugins.triager.llm]
criteria = "A company that could buy a vector database: it builds search, recommendation or RAG products."

[plugins.triager.llm.route]   # optional; default is the "ollama" backend
backend = "ollama"
model = "llama3.2:3b"
```

Add it from the CLI: `oaken plugin add llm`, then set `criteria` (the plugin will not start without it).
It runs last (rank 90), after `dedupe` and `rules`. The item reaches the model only as marked data; `criteria` are
your words and sit in the instructions. Try it without submitting anything: `oaken llm try --text '...'`.

shortcut: one question per item; rejects do not teach `nearjunk`.
