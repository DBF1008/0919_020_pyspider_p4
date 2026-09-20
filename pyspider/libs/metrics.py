#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
"""
Unified metrics collection for pyspider components.

Provides counter / gauge / histogram metric types, renders them in
Prometheus text exposition format, and exposes them via a lightweight
HTTP server with `/metrics` and `/health` endpoints.
"""

import json
import logging
import threading

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
except ImportError:  # Python 2
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn

logger = logging.getLogger('metrics')

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _escape_label_value(value):
    return str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def _format_labels(labelnames, labelvalues):
    if not labelnames:
        return ''
    pairs = ','.join(
        '%s="%s"' % (name, _escape_label_value(value))
        for name, value in zip(labelnames, labelvalues)
    )
    return '{%s}' % pairs


class MetricChild(object):
    """A metric bound to a specific label set."""

    def __init__(self, metric, labelvalues):
        self._metric = metric
        self._labelvalues = labelvalues

    def inc(self, amount=1):
        self._metric._inc(self._labelvalues, amount)

    def dec(self, amount=1):
        self._metric._inc(self._labelvalues, -amount)

    def set(self, value):
        self._metric._set(self._labelvalues, value)

    def observe(self, value):
        self._metric._observe(self._labelvalues, value)


class BaseMetric(object):
    metric_type = 'untyped'

    def __init__(self, name, documentation='', labelnames=()):
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self._values = {}
        self._lock = threading.Lock()

    def labels(self, *labelvalues, **labelkwargs):
        """Bind the metric to a label set."""
        if labelkwargs:
            labelvalues = tuple(labelkwargs.get(name, '') for name in self.labelnames)
        labelvalues = tuple(labelvalues)
        if len(labelvalues) != len(self.labelnames):
            raise ValueError('expected %d label values, got %d' % (
                len(self.labelnames), len(labelvalues)))
        return MetricChild(self, labelvalues)

    def _default_labels(self):
        if self.labelnames:
            raise ValueError('metric %s requires labels: %r' % (self.name, self.labelnames))
        return ()

    def _inc(self, labelvalues, amount):
        raise NotImplementedError

    def _set(self, labelvalues, value):
        raise NotImplementedError

    def _observe(self, labelvalues, value):
        raise NotImplementedError

    def _render_samples(self):
        """Yield (suffix, labelvalues, value) tuples."""
        raise NotImplementedError

    def render(self):
        lines = []
        if self.documentation:
            lines.append('# HELP %s %s' % (self.name, self.documentation))
        lines.append('# TYPE %s %s' % (self.name, self.metric_type))
        for suffix, labelvalues, value in self._render_samples():
            lines.append('%s%s%s %s' % (
                self.name, suffix,
                _format_labels(self.labelnames, labelvalues),
                float(value) if isinstance(value, float) else value,
            ))
        return '\n'.join(lines)


class Counter(BaseMetric):
    metric_type = 'counter'

    def inc(self, amount=1):
        self._inc(self._default_labels(), amount)

    def _inc(self, labelvalues, amount):
        with self._lock:
            self._values[labelvalues] = self._values.get(labelvalues, 0) + amount

    def _render_samples(self):
        with self._lock:
            items = sorted(self._values.items())
        for labelvalues, value in items:
            yield '', labelvalues, value


class Gauge(BaseMetric):
    metric_type = 'gauge'

    def inc(self, amount=1):
        self._inc(self._default_labels(), amount)

    def dec(self, amount=1):
        self._inc(self._default_labels(), -amount)

    def set(self, value):
        self._set(self._default_labels(), value)

    def _inc(self, labelvalues, amount):
        with self._lock:
            self._values[labelvalues] = self._values.get(labelvalues, 0) + amount

    def _set(self, labelvalues, value):
        with self._lock:
            self._values[labelvalues] = value

    def _render_samples(self):
        with self._lock:
            items = sorted(self._values.items())
        for labelvalues, value in items:
            yield '', labelvalues, value


class Histogram(BaseMetric):
    metric_type = 'histogram'

    def __init__(self, name, documentation='', labelnames=(), buckets=DEFAULT_BUCKETS):
        BaseMetric.__init__(self, name, documentation, labelnames)
        self.buckets = tuple(sorted(buckets))

    def observe(self, value):
        self._observe(self._default_labels(), value)

    def _observe(self, labelvalues, value):
        with self._lock:
            bucket_counts, total_sum, total_count = self._values.get(
                labelvalues, ([0] * len(self.buckets), 0.0, 0))
            for i, boundary in enumerate(self.buckets):
                if value <= boundary:
                    bucket_counts[i] += 1
            total_sum += value
            total_count += 1
            self._values[labelvalues] = (bucket_counts, total_sum, total_count)

    def _render_samples(self):
        with self._lock:
            items = sorted(self._values.items())
        for labelvalues, (bucket_counts, total_sum, total_count) in items:
            for boundary, count in zip(self.buckets, bucket_counts):
                yield '_bucket', labelvalues + ('%g' % boundary, ), count
            yield '_bucket', labelvalues + ('+Inf', ), total_count
            yield '_sum', labelvalues, total_sum
            yield '_count', labelvalues, total_count

    def render(self):
        lines = []
        if self.documentation:
            lines.append('# HELP %s %s' % (self.name, self.documentation))
        lines.append('# TYPE %s %s' % (self.name, self.metric_type))
        for suffix, labelvalues, value in self._render_samples():
            names = self.labelnames + ('le', ) if suffix == '_bucket' else self.labelnames
            lines.append('%s%s%s %s' % (
                self.name, suffix,
                _format_labels(names, labelvalues),
                float(value) if isinstance(value, float) else value,
            ))
        return '\n'.join(lines)


class MetricsRegistry(object):
    """A registry of metrics, renders Prometheus text exposition format."""

    def __init__(self):
        self._metrics = {}
        self._lock = threading.Lock()

    def _get_or_create(self, cls, name, documentation='', labelnames=(), **kwargs):
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                metric = cls(name, documentation, labelnames, **kwargs)
                self._metrics[name] = metric
            elif not isinstance(metric, cls):
                raise ValueError('metric %r already registered with another type' % name)
            return metric

    def counter(self, name, documentation='', labelnames=()):
        return self._get_or_create(Counter, name, documentation, labelnames)

    def gauge(self, name, documentation='', labelnames=()):
        return self._get_or_create(Gauge, name, documentation, labelnames)

    def histogram(self, name, documentation='', labelnames=(), buckets=DEFAULT_BUCKETS):
        return self._get_or_create(Histogram, name, documentation, labelnames,
                                   buckets=buckets)

    def render_prometheus(self):
        """Render all metrics in Prometheus text exposition format."""
        with self._lock:
            metrics = list(self._metrics.values())
        return '\n'.join(metric.render() for metric in metrics) + '\n'


default_registry = MetricsRegistry()


class _MetricsRequestHandler(BaseHTTPRequestHandler):
    registry = default_registry
    health_check = None

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/metrics':
            body = self.registry.render_prometheus().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == '/health':
            healthy, message = True, 'ok'
            if self.health_check is not None:
                try:
                    result = self.health_check()
                    if isinstance(result, tuple):
                        healthy, message = result
                    else:
                        healthy = bool(result)
                except Exception as e:
                    healthy, message = False, str(e)
            body = json.dumps({
                'status': 'ok' if healthy else 'fail',
                'message': message,
            }).encode('utf-8')
            self.send_response(200 if healthy else 503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()

    def log_message(self, format, *args):
        logger.debug('metrics http: ' + format, *args)


class MetricsHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_metrics_server(host, port, registry=None, health_check=None):
    """
    Start a metrics HTTP server in a daemon thread.

    Exposes `GET /metrics` (Prometheus text format) and `GET /health`.
    Returns the server object, call `shutdown()` to stop it.
    """
    handler = type('MetricsRequestHandler', (_MetricsRequestHandler,), {
        'registry': registry or default_registry,
        'health_check': health_check,
    })
    server = MetricsHTTPServer((host, int(port)), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    logger.info('metrics http server listening on %s:%s', host, port)
    return server


class MetricsServerMixin(object):
    """
    Mixin for components to expose metrics and health check over HTTP.

    Set `metrics_port` (and optionally `metrics_host`) to enable.
    """
    metrics_host = '0.0.0.0'
    metrics_port = 0
    _metrics_server = None

    def start_metrics_server(self, health_check=None, registry=None):
        if not self.metrics_port or self._metrics_server is not None:
            return
        self._metrics_server = start_metrics_server(
            self.metrics_host, self.metrics_port,
            registry=registry, health_check=health_check)

    def stop_metrics_server(self):
        if self._metrics_server is not None:
            self._metrics_server.shutdown()
            self._metrics_server = None
