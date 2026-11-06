# oaken-triager-rules

Your own deterministic triage rules for [Oaken Core](https://github.com/dfoulks1/oaken-core)'s intake,
ahead of any model. Ported from oaken-mail's `rules` triager with generic conditions: an item is a
source, a body (text or JSON) and labels.

```toml
[plugins.triager.rules]
[[plugins.triager.rules.rules]]
name = "hiring signal"
when = { source = "voltpop*", field_equals = { signal = "hiring" } }
action = "accept"                  # accept (default), reject or unsure
labels = { priority = "high" }

[[plugins.triager.rules.rules]]
name = "internal noise"
when = { field_contains = { company = "test" } }
action = "reject"
learn = true                       # remember its fingerprint
```

Rules run in the order listed; the first whose conditions all hold decides; an item none matches goes to
the next triager. Conditions: `source`, `body_contains`, `field_equals`, `field_contains`,
`field_present` (dotted paths into a JSON body), `has_label` (`key` or `key=value`).

```bash
oaken rules list
oaken rules test --source voltpop --body '{"signal": "hiring"}'    # which rule, what it would decide
```

Add it with `oaken plugin add oaken-triager-rules` (the repo's directory must be listed in
`[extensions] dirs`).

Apache-2.0. AI-assisted; see `prompts/`.
