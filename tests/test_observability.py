#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Created on 2026-09-20

"""
Tests for structured logging, request_id tracing, unified metrics
(Prometheus /metrics and /health endpoints) and graceful shutdown.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import unittest

from six.moves import queue as Queue
from six.moves.urllib.request import urlopen

from pyspider.libs import metrics as metrics_lib
from pyspider.libs import slog, utils


class TestMetrics(unittest.TestCase):

    def test_10_counter(self):
        counter = metrics_lib.Counter('test_counter', 'doc')
        counter.inc(project='p1')
        counter.inc(2, project='p1')
        counter.inc(project='p2')
        self.assertEqual(counter.get(project='p1'), 3)
        self.assertEqual(counter.get(project='p2'), 1)
        text = '\n'.join(counter.collect())
        self.assertIn('# TYPE test_counter counter', text)
        self.assertIn('test_counter{project="p1"} 3.0', text)

    def test_20_gauge(self):
        gauge = metrics_lib.Gauge('test_gauge')
        gauge.set(5)
        gauge.inc()
        gauge.dec(2)
        self.assertEqual(gauge.get(), 4)
        text = '\n'.join(gauge.collect())
        self.assertIn('# TYPE test_gauge gauge', text)
        self.assertIn('test_gauge 4.0', text)

    def test_30_histogram(self):
        histogram = metrics_lib.Histogram('test_histogram', buckets=(0.1, 1.0, float('inf')))
        histogram.observe(0.05, project='p1')
        histogram.observe(0.5, project='p1')
        histogram.observe(5.0, project='p1')
        stat = histogram.get(project='p1')
        self.assertEqual(stat['count'], 3)
        self.assertAlmostEqual(stat['sum'], 5.55)
        text = '\n'.join(histogram.collect())
        self.assertIn('# TYPE test_histogram histogram', text)
        self.assertIn('test_histogram_bucket{le="0.1",project="p1"} 1', text)
        self.assertIn('test_histogram_bucket{le="1.0",project="p1"} 2', text)
        self.assertIn('test_histogram_bucket{le="+Inf",project="p1"} 3', text)
        self.assertIn('test_histogram_count{project="p1"} 3', text)

    def test_40_registry_render(self):
        registry = metrics_lib.MetricsRegistry('test')
        registry.counter('a_total').inc()
        registry.gauge('b').set(1)
        registry.histogram('c_seconds').observe(0.1)
        text = registry.render_prometheus()
        self.assertIn('a_total 1.0', text)
        self.assertIn('b 1.0', text)
        self.assertIn('c_seconds_count 1', text)
        self.assertTrue(text.endswith('\n'))

    def test_50_metrics_http_server(self):
        registry = metrics_lib.MetricsRegistry('test')
        registry.counter('http_requests_total').inc(path='/')
        try:
            server = metrics_lib.start_metrics_server('127.0.0.1', 25391, registry)
        except (PermissionError, OSError) as e:
            self.skipTest('cannot bind port in this environment: %r' % e)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        body = urlopen('http://127.0.0.1:%d/metrics' % port, timeout=5).read().decode('utf-8')
        self.assertIn('http_requests_total{path="/"} 1.0', body)

        body = urlopen('http://127.0.0.1:%d/health' % port, timeout=5).read().decode('utf-8')
        health = json.loads(body)
        self.assertEqual(health['status'], 'ok')
        self.assertEqual(health['component'], 'test')
        self.assertIn('uptime', health)

        try:
            urlopen('http://127.0.0.1:%d/notfound' % port, timeout=5)
            self.fail('should return 404')
        except Exception as e:
            self.assertEqual(getattr(e, 'code', None), 404)

    def test_60_metrics_server_disabled(self):
        registry = metrics_lib.MetricsRegistry('test')
        self.assertIsNone(metrics_lib.start_metrics_server('127.0.0.1', 0, registry))


class TestSlog(unittest.TestCase):

    def setUp(self):
        slog.clear_request_id()

    def test_10_request_id_context(self):
        self.assertIsNone(slog.get_request_id())
        slog.set_request_id('abc')
        self.assertEqual(slog.get_request_id(), 'abc')
        slog.clear_request_id()
        self.assertIsNone(slog.get_request_id())

    def test_20_ensure_request_id(self):
        task = {'project': 'p', 'taskid': 't'}
        rid = slog.ensure_request_id(task)
        self.assertTrue(rid)
        self.assertEqual(task['request_id'], rid)
        self.assertEqual(slog.get_request_id(), rid)
        # existing request_id is kept
        rid2 = slog.ensure_request_id(task)
        self.assertEqual(rid, rid2)

    def test_30_structured_log_format(self):
        records = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger('test_slog')
        logger.addHandler(ListHandler())
        logger.setLevel(logging.INFO)
        slog.set_request_id('req-1')
        slog.slog(logger, logging.INFO, 'task_done', project='demo', url='http://a b/',
                  latency=0.5, empty=None)
        self.assertEqual(len(records), 1)
        line = records[0]
        self.assertIn('event=task_done', line)
        self.assertIn('request_id=req-1', line)
        self.assertIn('project=demo', line)
        self.assertIn('url="http://a b/"', line)
        self.assertIn('latency=0.5000', line)
        self.assertIn('empty=-', line)

    def test_40_task_log_fields(self):
        task = {'project': 'p', 'taskid': 't', 'url': 'u', 'other': 1}
        fields = slog.task_log_fields(task)
        self.assertEqual(fields, {'project': 'p', 'taskid': 't', 'url': 'u'})


class TestGracefulShutdownHelper(unittest.TestCase):

    def test_10_register_in_main_thread(self):
        called = []

        def handler(signum, frame):
            called.append(signum)

        ok = utils.register_graceful_shutdown(handler, signals=[signal.SIGUSR1]
                                              if hasattr(signal, 'SIGUSR1') else None)
        if not hasattr(signal, 'SIGUSR1'):
            self.skipTest('SIGUSR1 not available')
        self.assertTrue(ok)
        os.kill(os.getpid(), signal.SIGUSR1)
        self.assertEqual(len(called), 1)

    def test_20_register_in_non_main_thread(self):
        result = []

        def target():
            result.append(utils.register_graceful_shutdown(lambda *a: None))

        thread = threading.Thread(target=target)
        thread.start()
        thread.join()
        self.assertEqual(result, [False])


class TestProcessorObservability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from pyspider.database.local.projectdb import ProjectDB
            from pyspider.processor import Processor
        except ImportError as e:
            raise unittest.SkipTest('processor dependencies not available: %r' % e)
        cls.projectdb = ProjectDB([os.path.join(
            os.path.dirname(__file__), 'data_fetcher_processor_handler.py')])
        cls.status_queue = Queue.Queue()
        cls.newtask_queue = Queue.Queue()
        cls.result_queue = Queue.Queue()
        cls.processor = Processor(projectdb=cls.projectdb,
                                  inqueue=None,
                                  status_queue=cls.status_queue,
                                  newtask_queue=cls.newtask_queue,
                                  result_queue=cls.result_queue)

    def test_10_on_task_request_id_and_metrics(self):
        task = {
            'taskid': 'taskid',
            'project': 'data_fetcher_processor_handler',
            'url': 'http://example.com/',
            'request_id': 'trace-id-1',
            'process': {'callback': 'json'},
        }
        response = {
            'status_code': 200,
            'orig_url': 'http://example.com/',
            'url': 'http://example.com/',
            'headers': {},
            'content': '{"a": 1}',
            'cookies': {},
            'time': 0.1,
        }
        self.processor.on_task(task, response)

        # request_id is propagated into status pack
        status_pack = self.status_queue.get_nowait()
        self.assertEqual(status_pack['request_id'], 'trace-id-1')
        self.assertTrue(status_pack['track']['process']['ok'])

        # metrics are collected
        self.assertGreaterEqual(
            self.processor._metric_tasks.get(
                project='data_fetcher_processor_handler', status='success'), 1)
        stat = self.processor._metric_latency.get(
            project='data_fetcher_processor_handler')
        self.assertGreaterEqual(stat['count'], 1)
        text = self.processor.metrics.render_prometheus()
        self.assertIn('processor_tasks_total', text)
        self.assertIn('processor_task_duration_seconds', text)

    def test_20_on_task_generates_request_id(self):
        task = {
            'taskid': 'taskid2',
            'project': 'data_fetcher_processor_handler',
            'url': 'http://example.com/',
            'process': {'callback': 'json'},
        }
        response = {
            'status_code': 200,
            'orig_url': 'http://example.com/',
            'url': 'http://example.com/',
            'headers': {},
            'content': '{}',
            'cookies': {},
            'time': 0.1,
        }
        self.processor.on_task(task, response)
        self.assertTrue(task.get('request_id'))
        status_pack = self.status_queue.get_nowait()
        self.assertEqual(status_pack['request_id'], task['request_id'])

    def test_30_failed_task_metrics(self):
        task = {
            'taskid': 'taskid3',
            'project': 'data_fetcher_processor_handler',
            'url': 'http://example.com/',
            'process': {'callback': 'not_exist_callback'},
        }
        response = {
            'status_code': 200,
            'orig_url': 'http://example.com/',
            'url': 'http://example.com/',
            'headers': {},
            'content': '{}',
            'cookies': {},
            'time': 0.1,
        }
        self.processor.on_task(task, response)
        self.assertGreaterEqual(
            self.processor._metric_tasks.get(
                project='data_fetcher_processor_handler', status='failed'), 1)


class TestFetcherObservability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from pyspider.fetcher import Fetcher
        except ImportError as e:
            raise unittest.SkipTest('fetcher dependencies not available: %r' % e)
        cls.fetcher = Fetcher(None, None, async_mode=False)

    def test_10_async_fetch_metrics_and_request_id(self):
        task = {
            'taskid': 'taskid',
            'project': 'project',
            'url': 'data:,hello',
            'request_id': 'trace-fetch-1',
        }
        result = self.fetcher.fetch(task)
        self.assertEqual(result['status_code'], 200)
        # request_id kept
        self.assertEqual(task['request_id'], 'trace-fetch-1')
        # metrics collected
        self.assertGreaterEqual(
            self.fetcher._metric_requests.get(
                project='project', type='data', status='success'), 1)
        stat = self.fetcher._metric_latency.get(project='project')
        self.assertGreaterEqual(stat['count'], 1)
        text = self.fetcher.metrics.render_prometheus()
        self.assertIn('fetcher_requests_total', text)
        self.assertIn('fetcher_request_duration_seconds', text)

    def test_20_async_fetch_generates_request_id(self):
        task = {
            'taskid': 'taskid',
            'project': 'project',
            'url': 'data:,hello',
        }
        self.fetcher.fetch(task)
        self.assertTrue(task.get('request_id'))

    def test_30_graceful_quit_waits_inflight(self):
        from pyspider.fetcher import Fetcher
        fetcher = Fetcher(Queue.Queue(), Queue.Queue())
        thread = utils.run_in_thread(fetcher.run)
        time.sleep(0.5)
        self.assertTrue(thread.is_alive())
        fetcher.graceful_quit(timeout=5)
        thread.join(10)
        self.assertFalse(thread.is_alive())

    def test_40_health_check(self):
        health = self.fetcher._health_check()
        self.assertEqual(health['status'], 'ok')
        self.assertEqual(health['component'], 'fetcher')
        self.assertIn('inflight', health)


class TestSchedulerObservability(unittest.TestCase):

    def _make_scheduler(self, path):
        try:
            from pyspider.scheduler.scheduler import Scheduler
            from pyspider.database.sqlite import taskdb, projectdb, resultdb
        except ImportError as e:
            self.skipTest('scheduler dependencies not available: %r' % e)
        scheduler = Scheduler(
            taskdb=taskdb.TaskDB(path + '/task.db'),
            projectdb=projectdb.ProjectDB(path + '/project.db'),
            resultdb=resultdb.ResultDB(path + '/result.db'),
            newtask_queue=Queue.Queue(),
            status_queue=Queue.Queue(),
            out_queue=Queue.Queue(),
            data_path=path,
        )
        return scheduler

    def test_10_health_check_and_metrics(self):
        import shutil
        path = '/tmp/pyspider_test_observability'
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path)
        self.addCleanup(shutil.rmtree, path, True)
        scheduler = self._make_scheduler(path)
        health = scheduler._health_check()
        self.assertEqual(health['status'], 'ok')
        self.assertEqual(health['component'], 'scheduler')
        self.assertIn('projects', health)

        # metrics registry exists with expected series
        text = scheduler.metrics.render_prometheus()
        self.assertIn('scheduler_tasks_total', text)
        self.assertIn('scheduler_task_duration_seconds', text)
        self.assertIn('scheduler_inqueue_tasks', text)

    def test_20_on_select_task_injects_request_id(self):
        import shutil
        path = '/tmp/pyspider_test_observability2'
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path)
        self.addCleanup(shutil.rmtree, path, True)
        scheduler = self._make_scheduler(path)
        scheduler.projectdb.insert('test_project', {
            'name': 'test_project',
            'group': 'group',
            'status': 'DEBUG',
            'script': 'import time\nprint(time.time())',
            'rate': 1.0,
            'burst': 10,
        })
        scheduler._update_projects()
        # drain the _on_get_info task sent by project update
        while not scheduler.out_queue.empty():
            scheduler.out_queue.get_nowait()
        task = {
            'taskid': 'taskid1',
            'project': 'test_project',
            'url': 'http://example.com/',
        }
        scheduler.on_select_task(task)
        # request_id injected for full-link tracing
        self.assertTrue(task.get('request_id'))
        # task dispatched to fetcher queue
        sent = scheduler.out_queue.get_nowait()
        self.assertEqual(sent['request_id'], task['request_id'])
        # metrics collected
        self.assertGreaterEqual(
            scheduler._metric_tasks.get(project='test_project', status='selected'), 1)


@unittest.skipUnless(hasattr(signal, 'SIGTERM'), 'SIGTERM not available')
class TestComponentSignalHandling(unittest.TestCase):
    """Send real SIGTERM to components running in subprocess."""

    def _check_importable(self, module):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import %s' % module],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=repo)
        _, err = proc.communicate()
        if proc.returncode != 0:
            self.skipTest('%s not importable in this environment: %s'
                          % (module, err.decode('utf-8', 'replace').strip().splitlines()[-1]
                             if err else ''))

    def _run_component(self, code):
        env = dict(os.environ)
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env['PYTHONPATH'] = repo + os.pathsep + env.get('PYTHONPATH', '')
        proc = subprocess.Popen(
            [sys.executable, '-c', code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        time.sleep(2)
        self.assertIsNone(proc.poll(), 'component exited unexpectedly')
        proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail('component did not exit gracefully after SIGTERM')
        self.assertEqual(proc.returncode, 0, err.decode('utf-8', 'replace'))
        return err.decode('utf-8', 'replace')

    def test_10_processor_sigterm(self):
        self._check_importable('pyspider.processor.processor')
        output = self._run_component('''
import logging
logging.basicConfig(level=logging.INFO)
from six.moves import queue as Queue
from pyspider.database.local.projectdb import ProjectDB
from pyspider.processor import Processor
p = Processor(projectdb=ProjectDB([]), inqueue=Queue.Queue(),
              status_queue=Queue.Queue(), newtask_queue=Queue.Queue(),
              result_queue=Queue.Queue())
p.run()
''')
        self.assertIn('component_start', output)
        self.assertIn('component_exit', output)

    def test_20_fetcher_sigterm(self):
        self._check_importable('pyspider.fetcher.tornado_fetcher')
        output = self._run_component('''
import logging
logging.basicConfig(level=logging.INFO)
from six.moves import queue as Queue
from pyspider.fetcher import Fetcher
f = Fetcher(Queue.Queue(), Queue.Queue())
f.run()
''')
        self.assertIn('component_start', output)
        self.assertIn('component_exit', output)

    def test_30_scheduler_sigterm(self):
        self._check_importable('pyspider.scheduler.scheduler')
        output = self._run_component('''
import logging, os, tempfile
logging.basicConfig(level=logging.INFO)
from six.moves import queue as Queue
from pyspider.scheduler.scheduler import Scheduler
from pyspider.database.sqlite import taskdb, projectdb
path = tempfile.mkdtemp()
s = Scheduler(taskdb=taskdb.TaskDB(os.path.join(path, 'task.db')),
              projectdb=projectdb.ProjectDB(os.path.join(path, 'project.db')),
              newtask_queue=Queue.Queue(), status_queue=Queue.Queue(),
              out_queue=Queue.Queue(), data_path=path)
s.run()
''')
        self.assertIn('component_start', output)
        self.assertIn('component_exit', output)


if __name__ == '__main__':
    unittest.main()
