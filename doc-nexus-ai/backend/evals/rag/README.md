# RAG evaluation datasets

`dataset.schema.json` defines the JSONL case contract. `datasets/smoke-v1.jsonl`
contains synthetic references only and is safe to run in CI. Production
datasets, manifests, judgments and reports belong under `data/evals/rag/` and
must be generated from authorized, de-identified material; they are not stored
in Git.

The runner compares `legacy` and `auto` source references. It stores aggregate
metrics and a code/dataset revision, never questions, answers, prompts or
excerpts. An `eligible` decision is required before changing production
routing; a smoke run is not evidence for that decision.
