# Context

Written with Claude (Opus 5.5) in a Claude Code session on oaken-plugins, 2026-11-16, at Shane Curcuru's direction: "plan and propose a
PR to add a plugin (if needed) to allow oaken to work with LMStudio's Bionic application as an alternate provider to ollama." Shane reviews it
before he opens the pull request; the maintainer has not reviewed it until it is merged.

Covers `llm_backend_lmstudio` (the backend and the shared `client.py`) and `embedder_lmstudio`. Built on
`oaken_core.plugins.base.BaseLLMBackend` and `BaseEmbedder`, mirroring `llm_backend_ollama` and `embedder_ollama`: the same retry rules,
the same `off_machine` report from `check_url`, the same "never pull, never leak a body" care.

The API shapes come from LM Studio's documentation (REST `/api/v1/models` and `/api/v0/models`, OpenAI compatibility, structured output,
the API changelog for reasoning fields, authentication). The pretend server in `tests/lmstudio_fake.py` is written from those docs, not
recorded from a running LM Studio. No third-party code was copied.

Choices worth knowing:

- Named `lmstudio`, not a generic `openai`, because the model listing, the loaded context and the token are LM Studio's own. The
  generation path is plain OpenAI, so a generic backend can reuse `LMStudioClient` later (PLAN.md expects one).
- The context is the loaded instance's length: LM Studio truncates to what it loaded, so the model's maximum would over-budget.
- LM Studio's OpenAI chat route has no documented per-request reasoning switch, so `think` is recorded in the trace and not sent.
- `trust_env` is off for loopback URLs, so a proxy variable cannot carry text that `off_machine = False` promised stays here.
