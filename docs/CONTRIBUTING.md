# Working on FORMA

* `make check` (or `.\scripts\dev.ps1 lint` + `test`) must be green before a commit: ruff, mypy, pytest.
* Never hard-code a target field name — go through `SchemaRegistry`.
* A new transformation = DSL op + compiler branch + unit test + one line in `docs/MAPPING_DSL.md`.
* Change the profiler/heuristics/engine? Run `make eval` — field accuracy on the corpus must not drop.
* Runtime data lives under `data/` and is git-ignored; `make reset` wipes it, `make seed` rebuilds the demo.
* Commit messages: conventional (`feat:`, `fix:`, `docs:`, `test:`).
