#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:

import json
import unittest

try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen

from pyspider.libs import metrics


class TestMetrics(unittest.TestCase):

    def setUp(self):
        self.registry = metrics.MetricsRegistry()

    def test_010_counter(self):
        c = self.registry.counter('test_counter_total', 'a counter')
        c.inc()
        c.inc(2)
        text = self.registry.render_prometheus()
        self.assertIn('# TYPE test_counter_total counter', text)
        self.assertIn('test_counter_total 3', text)

    def test_020_counter_with_labels(self):
        c = self.registry.counter('test_labeled_total', 'a counter', ('project', 'status'))
        c.labels('proj1', 'success').inc()
        c.labels(project='proj1', status='failed').inc(2)
        text = self.registry.render_prometheus()
        self.assertIn('test_labeled_total{project="proj1",status="success"} 1', text)
        self.assertIn('test_labeled_total{project="proj1",status="failed"} 2', text)

    def test_030_label_count_mismatch(self):
        c = self.registry.counter('test_mismatch_total', 'a counter', ('a', 'b'))
        self.assertRaises(ValueError, c.labels, 'only-one')

    def test_040_gauge(self):
        g = self.registry.gauge('test_gauge', 'a gauge')
        g.set(10)
        g.inc(5)
        g.dec(3)
        text = self.registry.render_prometheus()
        self.assertIn('# TYPE test_gauge gauge', text)
        self.assertIn('test_gauge 12', text)

    def test_050_histogram(self):
        h = self.registry.histogram('test_latency_seconds', 'latency',
                                    ('stage', ), buckets=(0.1, 0.5, 1.0))
        h.labels('fetch').observe(0.05)
        h.labels('fetch').observe(0.3)
        h.labels('fetch').observe(5.0)
        text = self.registry.render_prometheus()
        self.assertIn('# TYPE test_latency_seconds histogram', text)
        self.assertIn('test_latency_seconds_bucket{stage="fetch",le="0.1"} 1', text)
        self.assertIn('test_latency_seconds_bucket{stage="fetch",le="0.5"} 2', text)
        self.assertIn('test_latency_seconds_bucket{stage="fetch",le="1"} 2', text)
        self.assertIn('test_latency_seconds_bucket{stage="fetch",le="+Inf"} 3', text)
        self.assertIn('test_latency_seconds_count{stage="fetch"} 3', text)
        self.assertIn('test_latency_seconds_sum{stage="fetch"} 5.35', text)

    def test_060_duplicate_registration(self):
        c1 = self.registry.counter('test_dup_total', 'a counter')
        c2 = self.registry.counter('test_dup_total', 'a counter')
        self.assertIs(c1, c2)
        self.assertRaises(ValueError, self.registry.gauge, 'test_dup_total', 'a gauge')

    def test_070_label_escape(self):
        c = self.registry.counter('test_escape_total', 'a counter', ('label', ))
        c.labels('a"b\nc').inc()
        text = self.registry.render_prometheus()
        self.assertIn('test_escape_total{label="a\\"b\\nc"} 1', text)


class TestMetricsHTTPServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.registry = metrics.MetricsRegistry()
        cls.registry.counter('test_http_total', 'a counter').inc(42)
        cls.healthy = {'value': True}
        try:
            cls.server = metrics.start_metrics_server(
                '127.0.0.1', 0, registry=cls.registry,
                health_check=lambda: cls.healthy['value'])
        except EnvironmentError as e:
            raise unittest.SkipTest('cannot bind http server: %s' % e)
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_010_metrics_endpoint(self):
        body = urlopen('http://127.0.0.1:%d/metrics' % self.port).read().decode('utf-8')
        self.assertIn('test_http_total 42', body)

    def test_020_health_endpoint_ok(self):
        self.healthy['value'] = True
        body = urlopen('http://127.0.0.1:%d/health' % self.port).read().decode('utf-8')
        self.assertEqual(json.loads(body)['status'], 'ok')

    def test_030_health_endpoint_fail(self):
        self.healthy['value'] = False
        try:
            urlopen('http://127.0.0.1:%d/health' % self.port)
            self.fail('expected HTTPError 503')
        except Exception as e:
            self.assertEqual(getattr(e, 'code', None), 503)
        self.healthy['value'] = True

    def test_040_not_found(self):
        try:
            urlopen('http://127.0.0.1:%d/unknown' % self.port)
            self.fail('expected HTTPError 404')
        except Exception as e:
            self.assertEqual(getattr(e, 'code', None), 404)


if __name__ == '__main__':
    unittest.main()
