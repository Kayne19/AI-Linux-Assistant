Router evals live here.

Files:
- `router_eval_cases.json`: prompt cases for repeatable router evaluation
- `router_deep_eval_scenarios.json`: multi-turn deep-eval scenarios
- `last_router_eval_results.json`: last saved output from the eval runner
- `last_router_deep_eval_results.json`: last saved output from the deep eval runner
- `../scripts/eval/evaluate_router.py`: the runner
- `../scripts/eval/evaluate_router_deep.py`: the deep scenario runner

What the runner measures:
- router errors vs successful completion
- routing label match
- whether RAG was used or skipped as expected
- whether citations were present when expected
- tool names used
- state trace
- raw/summarized retrieved-doc sizes

What the runner does not measure:
- deep semantic correctness
- whether the final answer is the best possible answer
- whether a citation is truly the ideal citation

What the deep runner adds:
- multi-turn debugging scenarios
- restart persistence checks against the memory DB
- branch-switch challenges after contrary evidence
- final committed-memory and candidate dumps for each scenario
- per-step memory events and traces for human review

Use it for:
- prompt regression checks
- comparing provider/model mixes
- spotting routing/tool-use failures quickly
- evaluating with one reused router instance, which matches normal runtime much
  more closely than cold-starting the retrieval stack for every case

Default runtime behavior for evals:
- by default the script follows the normal app runtime for retrieval devices
- if you want to force CPU or GPU for evals, use the explicit device flags

Typical usage:

```bash
cd Back-end
python scripts/eval/evaluate_router.py
```

Compare models:

```bash
python scripts/eval/evaluate_router.py \
  --classifier-provider openai \
  --classifier-model gpt-4.1-mini \
  --responder-provider local \
  --responder-model qwen2.5:14b
```

Run the deeper scenario battery:

```bash
python scripts/eval/evaluate_router_deep.py
```

If you want to force retrieval devices during evals:

```bash
python scripts/eval/evaluate_router.py \
  --vectordb-embed-device cpu \
  --vectordb-rerank-device cpu
```
