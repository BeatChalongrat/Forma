# Real customer files for evaluation (ADR-041)

Everything in this folder except this README is git-ignored: real exports stay on the machine that
evaluates them.

```
data/real/
  C104_2026-10/                 one folder per customer delivery (name starts with the customer code)
    files/                      the files exactly as the customer sent them (xlsx / csv)
    expected_output.jsonl       the reviewed, correct output: approve in the UI, export JSONL, check it
    manifest.json               written by `forma eval --index` when missing (customer_code, files …)
```

Then, from `backend/`:

```
forma eval --corpus ../data/real --index     # build index.json from the folders, score the heuristic mapper
forma eval --corpus ../data/real --llm       # the same files through the configured AI provider
forma eval --corpus ../data/real --replay    # correction replay on sequences (round_no / sequence in manifest.json)
```

A sequence (the same customer over several deliveries) is declared in each folder's `manifest.json` with
`"sequence": "<name>"` and `"round_no": 0, 1, 2 …`; `expected_relation` may be `same_customer_unchanged`,
`renamed` or `wrong_file` like the synthetic corpus. Anonymise what must be anonymised before copying files
here; the harness never uploads anything.
