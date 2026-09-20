#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Created on 2026-09-20

"""
Lightweight metrics collection with Prometheus text exposition format.

No external dependency: Counter/Gauge/Histogram are collected in a
thread-safe registry and exposed via a small HTTP server providing
`/metrics` (Prometheus format) and `/health` (JSON health check).
"""

import json
import threading
import time

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:  # Python 2
    from BaseHTTPServer import BaseHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from BaseHTTPServer import HTTPServer as ThreadingHTTPServer


def _label_key(labels):
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def _format_labels(label_key):
    if not label_key:
        return ''
    return '{%s}' % ','.join('%s="%s"' % (k, v) for k, v in label_key)


class Counter(object):
    """A monotonically increasing counter."""

    def __init__(self, name, documentation='', labels=()):
        self.name = name
        self.documentation = documentation
        self._values = {}
        self._lock = threading.Lock()
        for label_key in labels:
            self._values[label_key] = 0.0

    def inc(self, amount=1, **labels):
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def get(self, **labels):
        with self._lock:
            return self._values.get(_label_key(labels), 0.0)

    def collect(self):
        with self._lock:
            items = sorted(self._values.items())
        lines = [
            '# HELP %s %s' % (self.name, self.documentation or self.name),
            '# TYPE %s counter' % self.name,
        ]
        for label_key, value in items:
            lines.append('%s%s %s' % (self.name, _format_labels(label_key), value))
        return lines


class Gauge(object):
    """A gauge that can go up and down."""

    def __init__(self, name, documentation='', labels=()):
        self.name = name
        self.documentation = documentation
        self._values = {}
        self._lock = threading.Lock()
        for label_key in labels:
            self._values[label_key] = 0.0

    def set(self, value, **labels):
        with self._lock:
            self._values[_label_key(labels)] = float(value)

    def inc(self, amount=1, **labels):
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount=1, **labels):
        self.inc(-amount, **labels)

    def get(self, **labels):
        with self._lock:
            return self._values.get(_label_key(labels), 0.0)

    def collect(self):
        with self._lock:
            items = sorted(self._values.items())
        lines = [
            '# HELP %s %s' % (self.name, self.documentation or self.name),
            '# TYPE %s gauge' % self.name,
        ]
        for label_key, value in items:
            lines.append('%s%s %s' % (self.name, _format_labels(label_key), value))
        return lines


class Histogram(object):
    """A histogram with fixed buckets, tracking count and sum."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
                       1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float('inf'))

    def __init__(self, name, documentation='', buckets=None, labels=()):
        self.name = name
        self.documentation = documentation
        self.buckets = tuple(buckets or self.DEFAULT_BUCKETS)
        self._values = {}
        self._lock = threading.Lock()
        for label_key in labels:
            self._values[label_key] = self._new_value()

    def _new_value(self):
        return {
            'buckets': [0] * len(self.buckets),
            'count': 0,
            'sum': 0.0,
        }

    def observe(self, value, **labels):
        key = _label_key(labels)
        with self._lock:
            if key not in self._values:
                self._values[key] = self._new_value()
            entry = self._values[key]
            for i, upper in enumerate(self.buckets):
                if value <= upper:
                    entry['buckets'][i] += 1
            entry['count'] += 1
            entry['sum'] += value

    def get(self, **labels):
        with self._lock:
            entry = self._values.get(_label_key(labels))
            if entry is None:
                return {'count': 0, 'sum': 0.0}
            return {'count': entry['count'], 'sum': entry['sum']}

    def collect(self):
        with self._lock:
            items = sorted(self._values.items())
        lines = [
            '# HELP %s %s' % (self.name, self.documentation or self.name),
            '# TYPE %s histogram' % self.name,
        ]
        for label_key, entry in items:
            base_labels = dict(label_key)
            for upper, count in zip(self.buckets, entry['buckets']):
                le = '+Inf' if upper == float('inf') else repr(upper)
                labels = dict(base_labels)
                labels['le'] = le
                lines.append('%s_bucket%s %s' % (
                    self.name, _format_labels(_label_key(labels)), count))
            lines.append('%s_count%s %s' % (
                self.name, _format_labels(label_key), entry['count']))
            lines.append('%s_sum%s %s' % (
                self.name, _format_labels(label_key), entry['sum']))
        return lines


class MetricsRegistry(object):
    """Thread-safe registry of metrics for one component."""

    def __init__(self, component='pyspider'):
        self.component = component
        self._metrics = {}
        self._lock = threading.Lock()
        self.start_time = time.time()

    def counter(self, name, documentation=''):
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = Counter(name, documentation)
            return self._metrics[name]

    def gauge(self, name, documentation=''):
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = Gauge(name, documentation)
            return self._metrics[name]

    def histogram(self, name, documentation='', buckets=None):
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = Histogram(name, documentation, buckets)
            return self._metrics[name]

    def render_prometheus(self):
        """Render all metrics in Prometheus text exposition format."""
        with self._lock:
            metrics = list(self._metrics.values())
        lines = []
        for metric in metrics:
            lines.extend(metric.collect())
        return '\n'.join(lines) + '\n'

    def health(self):
        """Basic health information of the component."""
        return {
            'status': 'ok',
            'component': self.component,
            'uptime': time.time() - self.start_time,
        }


class _MetricsHandler(BaseHTTPRequestHandler):
    registry = None
    health_check = None

    def do_GET(self):  # noqa: N802
        if self.path == '/metrics':
            body = self.registry.render_prometheus().encode('utf-8')
            self._respond(200, body, 'text/plain; version=0.0.4; charset=utf-8')
        elif self.path == '/health':
            if self.health_check is not None:
                payload = self.health_check()
            else:
                payload = self.registry.health()
            body = json.dumps(payload).encode('utf-8')
            self._respond(200, body, 'application/json')
        else:
            self._respond(404, b'not found\n', 'text/plain')

    def _respond(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start_metrics_server(host, port, registry, health_check=None, logger=None):
    """
    Start a HTTP server in a daemon thread exposing:

    - GET /metrics  Prometheus text exposition format
    - GET /health   JSON health check

    Returns the server instance (call .shutdown() to stop it),
    or None when port is falsy.
    """
    if not port:
        return None

    handler = type('MetricsHandler', (_MetricsHandler,), {
        'registry': registry,
        'health_check': health_check,
    })
    server = ThreadingHTTPServer((host, int(port)), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    if logger:
        logger.info('metrics server listening on %s:%s '
                    '(endpoints: /metrics, /health)', host, port)
    return server
