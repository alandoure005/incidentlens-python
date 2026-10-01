import io
import json
import unittest
from incidentlens import analyze, MAX_LINE_BYTES


def event(**changes):
    return {'timestamp': '2026-01-01T12:00:00Z', 'route': '/orders', 'status': 200, 'duration_ms': 20, **changes}


def stream(events):
    return io.BytesIO(('\n'.join(json.dumps(item) for item in events) + '\n').encode())


class AnalysisTests(unittest.TestCase):
    def test_error_budget_and_percentile_boundary(self):
        events = [event(duration_ms=50) for _ in range(19)] + [event(status=503, duration_ms=500)]
        summary = analyze(stream(events), error_budget=0.01)['summary']
        self.assertEqual(summary['p95_upper_bound_ms'], 50)
        self.assertEqual(summary['server_errors'], 1)
        self.assertEqual(summary['budget_consumption_ratio'], 5)
        self.assertTrue(summary['budget_exceeded'])

    def test_invalid_values_and_strict_mode(self):
        bad = [event(status=True), event(duration_ms=float('nan')), event(timestamp='2026-01-01'), [], event(route='bad')]
        report = analyze(stream(bad + [event()]))
        self.assertEqual(report['invalid_lines'], 5)
        self.assertEqual(report['summary']['requests'], 1)
        with self.assertRaises(ValueError):
            analyze(stream(bad), strict=True)

    def test_route_cardinality_is_bounded_and_queries_removed(self):
        events = [event(route='/a?token=example'), event(route='/a'), event(route='/b'), event(route='/c')]
        report = analyze(stream(events), max_routes=1)
        self.assertEqual(list(report['routes']), ['/a'])
        self.assertEqual(report['routes']['/a']['requests'], 2)
        self.assertEqual(report['overflow_routes']['requests'], 2)

    def test_oversized_line_does_not_swallow_next_record(self):
        raw = io.BytesIO(b'x' * (MAX_LINE_BYTES * 3) + b'\n' + json.dumps(event()).encode() + b'\n')
        report = analyze(raw)
        self.assertEqual(report['invalid_lines'], 1)
        self.assertEqual(report['lines'], 2)
        self.assertEqual(report['summary']['requests'], 1)

    def test_slow_requests_and_empty_input(self):
        summary = analyze(stream([event(duration_ms=20000)]))['summary']
        self.assertTrue(summary['p95_exceeds_10000ms'])
        self.assertIsNone(summary['p95_upper_bound_ms'])
        self.assertIsNone(analyze(io.BytesIO())['summary']['error_rate'])

    def test_timestamp_offsets_and_budget_equality(self):
        report = analyze(stream([event(timestamp='2026-01-01T07:00:00-05:00', status=503), event(timestamp='2026-01-01T13:00:00Z')]), error_budget=0.5)
        self.assertFalse(report['summary']['budget_exceeded'])
        self.assertEqual(report['window']['end'], '2026-01-01T13:00:00+00:00')

if __name__ == '__main__':
    unittest.main()
