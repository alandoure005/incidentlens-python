"""Bounded-memory JSONL service-log analysis with error-budget checks."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys

BUCKETS = (50, 100, 250, 500, 1000, 2500, 5000, 10000)
MAX_LINE_BYTES = 65536


class Aggregate:
    def __init__(self):
        self.count = self.errors = 0
        self.histogram = [0] * (len(BUCKETS) + 1)

    def add(self, status, duration):
        self.count += 1
        self.errors += status >= 500
        bucket = next((i for i, limit in enumerate(BUCKETS) if duration <= limit), len(BUCKETS))
        self.histogram[bucket] += 1

    def report(self):
        rank = math.ceil(self.count * 0.95)
        cumulative = 0
        p95 = None
        overflow = False
        if self.count:
            for i, count in enumerate(self.histogram):
                cumulative += count
                if cumulative >= rank:
                    if i < len(BUCKETS):
                        p95 = BUCKETS[i]
                    else:
                        overflow = True
                    break
        return {'requests': self.count, 'server_errors': self.errors,
                'error_rate': self.errors / self.count if self.count else None,
                'p95_upper_bound_ms': p95, 'p95_exceeds_10000ms': overflow,
                'histogram': {**{str(bound): self.histogram[i] for i, bound in enumerate(BUCKETS)},
                              '+Inf': self.histogram[-1]}}


def validate(event):
    if not isinstance(event, dict):
        raise ValueError('event must be an object')
    timestamp, route, status, duration = (event.get(key) for key in ('timestamp', 'route', 'status', 'duration_ms'))
    if not isinstance(timestamp, str):
        raise ValueError('timestamp must be an ISO 8601 string with timezone')
    parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    if not isinstance(route, str) or not route.startswith('/') or len(route) > 256 or any(ord(c) < 32 for c in route):
        raise ValueError('route must be a path with at most 256 characters')
    if type(status) is not int or not 100 <= status <= 599:
        raise ValueError('status must be an integer between 100 and 599')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 <= duration <= 86400000:
        raise ValueError('duration_ms must be finite, nonnegative, and at most one day')
    return parsed, route.split('?', 1)[0], status, duration


def analyze(stream, max_routes=1000, error_budget=0.01, strict=False):
    if type(max_routes) is not int or not 1 <= max_routes <= 10000:
        raise ValueError('max_routes must be between 1 and 10000')
    if not math.isfinite(error_budget) or not 0 <= error_budget <= 1:
        raise ValueError('error_budget must be from 0 to 1')
    total, overflow = Aggregate(), Aggregate()
    routes = {}
    invalid = lines = 0
    examples = []
    earliest = latest = None
    while True:
        raw = stream.readline(MAX_LINE_BYTES + 1)
        if not raw:
            break
        lines += 1
        try:
            if len(raw) > MAX_LINE_BYTES:
                while raw and not raw.endswith(b'\n'):
                    raw = stream.readline(MAX_LINE_BYTES + 1)
                raise ValueError('line exceeds 64 KiB')
            event = json.loads(raw)
            timestamp, route, status, duration = validate(event)
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            invalid += 1
            if len(examples) < 5:
                examples.append({'line': lines, 'error': str(error)[:160]})
            if strict:
                raise ValueError(f'invalid event at line {lines}: {error}') from error
            continue
        earliest = timestamp if earliest is None or timestamp < earliest else earliest
        latest = timestamp if latest is None or timestamp > latest else latest
        total.add(status, duration)
        if route not in routes and len(routes) < max_routes:
            routes[route] = Aggregate()
        routes.get(route, overflow).add(status, duration)
    summary = total.report()
    summary['error_budget'] = error_budget
    summary['budget_exceeded'] = total.count > 0 and total.errors / total.count > error_budget
    summary['budget_consumption_ratio'] = (total.errors / total.count / error_budget) if total.count and error_budget else None
    return {'schema_version': 1, 'lines': lines, 'invalid_lines': invalid, 'invalid_examples': examples,
            'window': {'start': earliest.isoformat() if earliest else None, 'end': latest.isoformat() if latest else None},
            'summary': summary, 'routes': {key: value.report() for key, value in sorted(routes.items())},
            'overflow_routes': overflow.report(),
            'notes': ['Histogram buckets are disjoint; p95 is an upper bound, not an exact percentile.',
                      'Use route templates such as /orders/:id to control cardinality.',
                      'Error budget is evaluated over this input sample, not a rolling SLO window.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', help='JSONL input, or - for stdin')
    parser.add_argument('--max-routes', type=int, default=1000)
    parser.add_argument('--error-budget', type=float, default=0.01)
    parser.add_argument('--strict', action='store_true')
    parser.add_argument('--fail-on-budget', action='store_true')
    args = parser.parse_args()
    try:
        if args.file == '-':
            report = analyze(sys.stdin.buffer, args.max_routes, args.error_budget, args.strict)
        else:
            with Path(args.file).open('rb') as stream:
                report = analyze(stream, args.max_routes, args.error_budget, args.strict)
        print(json.dumps(report, indent=2, allow_nan=False))
        if not report['summary']['requests']:
            return 2
        return 1 if args.fail_on_budget and report['summary']['budget_exceeded'] else 0
    except (OSError, ValueError) as error:
        print(f'incidentlens: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
