#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:

import json
import logging
import unittest

from pyspider.libs import log


class TestRequestId(unittest.TestCase):

    def tearDown(self):
        log.clear_request_id()

    def test_010_new_request_id(self):
        request_id = log.new_request_id()
        self.assertTrue(request_id)
        self.assertNotEqual(request_id, log.new_request_id())

    def test_020_context(self):
        self.assertIsNone(log.get_request_id())
        log.set_request_id('req-1')
        self.assertEqual(log.get_request_id(), 'req-1')
        log.clear_request_id()
        self.assertIsNone(log.get_request_id())

    def test_030_ensure_request_id_attach(self):
        task = {'taskid': 't1'}
        request_id = log.ensure_request_id(task)
        self.assertEqual(task['request_id'], request_id)
        self.assertEqual(log.get_request_id(), request_id)

    def test_040_ensure_request_id_keep_existing(self):
        task = {'taskid': 't1', 'request_id': 'req-keep'}
        request_id = log.ensure_request_id(task)
        self.assertEqual(request_id, 'req-keep')
        self.assertEqual(task['request_id'], 'req-keep')


class TestStructuredLog(unittest.TestCase):

    def setUp(self):
        self.records = []
        self.logger = logging.getLogger('test_structured_log')
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.handler = log.SaveLogHandler(self.records)
        self.logger.addHandler(self.handler)

    def tearDown(self):
        self.logger.removeHandler(self.handler)
        log.clear_request_id()

    def test_010_log_event_fields(self):
        log.log_event(self.logger, logging.INFO, 'fetch_done',
                      project='p1', status_code=200)
        self.assertEqual(len(self.records), 1)
        record = self.records[0]
        self.assertEqual(record.getMessage(), 'fetch_done')
        self.assertEqual(record.fields['project'], 'p1')
        self.assertEqual(record.fields['status_code'], 200)
        self.assertEqual(record.fields['event'], 'fetch_done')

    def test_020_log_event_attach_context_request_id(self):
        log.set_request_id('req-ctx')
        log.log_event(self.logger, logging.INFO, 'fetch_start')
        self.assertEqual(self.records[0].request_id, 'req-ctx')

    def test_030_log_event_explicit_request_id(self):
        log.log_event(self.logger, logging.INFO, 'fetch_start', request_id='req-explicit')
        self.assertEqual(self.records[0].request_id, 'req-explicit')

    def test_040_json_formatter(self):
        log.set_request_id('req-json')
        log.log_event(self.logger, logging.INFO, 'process_done',
                      project='p1', process_time=0.5)
        payload = json.loads(log.JsonFormatter().format(self.records[0]))
        self.assertEqual(payload['message'], 'process_done')
        self.assertEqual(payload['request_id'], 'req-json')
        self.assertEqual(payload['project'], 'p1')
        self.assertEqual(payload['process_time'], 0.5)
        self.assertEqual(payload['level'], 'INFO')


if __name__ == '__main__':
    unittest.main()
