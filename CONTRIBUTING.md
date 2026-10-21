# Contributing

Thanks for helping. lighttrace aims to stay small: one process, SQLite by default, plain HTML/CSS/JS
with no build step, and no SDK of its own. Changes that keep it that way are the easiest to merge.

## Develop

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

Postgres tests run when `LIGHTTRACE_TEST_PG` points at a **throwaway** database (the tests drop its
tables), e.g. `docker run --rm -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16` and
`LIGHTTRACE_TEST_PG=postgresql://postgres:pw@127.0.0.1:5432/postgres`. Framework end-to-end tests
(`tests/test_frameworks.py`) skip unless their framework is installed.

## Adding support for a framework or OTel dialect

1. Capture what the real exporter sends (run it against a local endpoint, no real LLM: fake or mock
   models) and save it under `tests/fixtures/` as OTLP/JSON. Remove host names, file paths and
   anything personal first.
2. Map its attributes in `lighttrace/otel.py`. Anything you don't map must still land in metadata.
3. Add one focused test in `tests/test_otel_dialects.py`, and a docs page or section in `docs/`.

## Pull requests

- Keep each PR to one change, with a test.
- Dependencies must be MIT, BSD or Apache-2.0 licensed (LGPL is fine as an optional extra).
  Heavy ones go behind an optional extra.

## Sign your commits (DCO)

Every commit must be signed off under the [Developer Certificate of Origin](https://developercertificate.org/):
by adding the line below you certify that you wrote the change or otherwise have the right to submit it
under the project's MIT license.

```
Signed-off-by: Your Name <you@example.com>
```

`git commit -s` adds it for you. Use your real name.
