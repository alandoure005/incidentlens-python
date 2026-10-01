# IncidentLens

[![CI](https://github.com/alandoure005/incidentlens-python/actions/workflows/ci.yml/badge.svg)](https://github.com/alandoure005/incidentlens-python/actions/workflows/ci.yml)

A streaming JSONL service-log analyzer that reports server-error rates, latency histogram bounds, and per-route summaries. It can fail a deployment check when an input sample exceeds an error budget.

## Quick start

Requires Python 3.10+. Uses only the standard library.

```sh
python3 -m unittest discover -s tests -v
python3 incidentlens.py examples/requests.jsonl
python3 incidentlens.py examples/requests.jsonl --error-budget 0.01 --fail-on-budget
```

The sample contains four requests and one server error, so the final command intentionally returns exit code `1`. Redirect JSON output to save a report:

```sh
python3 incidentlens.py examples/requests.jsonl > report.json
cat examples/requests.jsonl | python3 incidentlens.py - --strict
```

## Event contract

```json
{"timestamp":"2026-01-01T12:00:00Z","route":"/orders/:id","status":200,"duration_ms":42.5}
```

- `timestamp`: ISO 8601 with timezone.
- `route`: path up to 256 characters. Prefer templates rather than concrete IDs. Query strings are removed.
- `status`: integer HTTP status from 100 to 599; 500–599 count as server errors.
- `duration_ms`: finite, nonnegative number, no greater than one day.

Additional fields are ignored. Each physical line is limited to 64 KiB. Invalid events are counted and excluded; up to five diagnostic examples are retained. `--strict` rejects the input on the first invalid event. Empty input or input with no valid events returns `2`.

## Memory and aggregation

Each route stores counters and nine disjoint histogram buckets. The default maximum is 1,000 named routes; later unseen routes go into a separate overflow aggregate. Input is read in bounded chunks, including malformed oversized lines. Memory therefore does not grow with the total number of requests: it is proportional to the configured route count, bounded route lengths, and one input line.

Latency bounds are 50, 100, 250, 500, 1,000, 2,500, 5,000, and 10,000 ms, followed by an overflow bucket. `p95_upper_bound_ms` reports the bucket containing the nearest-rank 95th percentile, not an exact p95. A null bound with `p95_exceeds_10000ms=true` means the percentile is above the largest finite bucket.

The report includes the earliest and latest event times. The budget is evaluated across the supplied sample; this is not a rolling-window SLO monitor. Consumption ratio is observed error rate divided by the permitted rate. A zero budget produces a null ratio, while any server error still breaches the budget. Threshold equality is allowed.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Analysis completed; optional budget check passed |
| 1 | `--fail-on-budget` was set and the sample exceeded its budget |
| 2 | Invalid configuration, read failure, strict validation failure, or no valid events |

## Tests and further development

Tests cover percentile boundaries, invalid numeric values, strict mode, oversized-line recovery, timezone offsets, cardinality limits, and threshold equality. Future work includes rolling windows, Prometheus export, and fixtures from additional access-log formats. There is no hosted dashboard or alert delivery service in this version.
