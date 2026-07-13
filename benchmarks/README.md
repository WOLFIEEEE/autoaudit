# Benchmark corpus

Precision / recall measurement for rules. Each fixture is a local HTML
file with a known set of accessibility issues. The runner loads each
fixture, runs the audit, compares the found rules against the declared
ground truth, and produces a per-rule precision/recall table.

## Structure

    benchmarks/
      corpus/
        <name>/
          page.html             # the fixture
          ground_truth.yaml     # declared expected issues
      run_benchmark.py          # orchestrator + scoring

## ground_truth.yaml schema

```yaml
notes: |
  Human-readable description of what this fixture tests.
expected:
  - rule: sr-silent-interactive
    min_count: 1            # must fire at least this many times
    # Optional: max_count to catch over-firing.
    # Optional: severity / level for stricter checks.
forbidden:
  - rule: keyboard-no-focus-indicator
    # If this rule fires, the fixture is flagged as a FALSE POSITIVE
    # for the rule (the fixture is known-good for that particular
    # rule, even if it fails other rules).
```

## Running

    python benchmarks/run_benchmark.py

Produces a precision / recall table per rule. Exit code 1 if any
fixture reported an unexpected finding or missed an expected one.

## Adding fixtures

Keep fixtures minimal — one pattern per fixture, clearly documented.
Over time this becomes the empirical ground truth for rule quality.
