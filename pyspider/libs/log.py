#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Binux<i@binux.me>
#         http://binux.me
# Created on 2012-10-24 16:08:17

import logging

try:
    import curses
except ImportError:
    curses = None

import json
import threading
import uuid

try:
    from tornado.log import LogFormatter as _LogFormatter
except ImportError:
    _LogFormatter = None


if _LogFormatter is not None:

    class LogFormatter(_LogFormatter, object):
        """Init tornado.log.LogFormatter from logging.config.fileConfig"""
        def __init__(self, fmt=None, datefmt=None, color=True, *args, **kwargs):
            if fmt is None:
                fmt = _LogFormatter.DEFAULT_FORMAT
            super(LogFormatter, self).__init__(color=color, fmt=fmt, *args, **kwargs)

else:

    class LogFormatter(logging.Formatter):
        """Fallback formatter when tornado is not available"""
        DEFAULT_FORMAT = '%(color)s[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d]%(end_color)s %(message)s'

        def __init__(self, fmt=None, datefmt=None, color=True, *args, **kwargs):
            if fmt is None:
                fmt = '%[%(levelname)s %(asctime)s %(name)s] %(message)s'
            super(LogFormatter, self).__init__(fmt=fmt, datefmt=datefmt)


_request_context = threading.local()


def new_request_id():
    """Generate a new request id for tracing."""
    return uuid.uuid4().hex


def set_request_id(request_id):
    """Bind a request id to current thread context, None to clear."""
    _request_context.request_id = request_id


def get_request_id():
    """Get the request id bound to current thread context."""
    return getattr(_request_context, 'request_id', None)


def clear_request_id():
    """Clear the request id of current thread context."""
    _request_context.request_id = None


def ensure_request_id(task):
    """
    Get the request id of a task dict, attach a new one if missing,
    and bind it to current thread context.
    """
    request_id = None
    if isinstance(task, dict):
        request_id = task.get('request_id')
        if not request_id:
            request_id = new_request_id()
            task['request_id'] = request_id
    if not request_id:
        request_id = new_request_id()
    set_request_id(request_id)
    return request_id


class JsonFormatter(logging.Formatter):
    """Format log record as one-line JSON with request_id and extra fields."""

    def format(self, record):
        payload = {
            'time': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        request_id = getattr(record, 'request_id', None) or get_request_id()
        if request_id:
            payload['request_id'] = request_id
        fields = getattr(record, 'fields', None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload['exc_info'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def log_event(logger, level, event, **fields):
    """
    Emit a structured log line.

    The event name is used as log message, keyword arguments are attached
    as structured fields (rendered by JsonFormatter). The request id of
    current thread context is attached automatically.
    """
    extra = {'fields': dict(fields, event=event)}
    request_id = fields.pop('request_id', None) or get_request_id()
    if request_id:
        extra['request_id'] = request_id
    logger.log(level, event, extra=extra)


class SaveLogHandler(logging.Handler):
    """LogHandler that save records to a list"""

    def __init__(self, saveto=None, *args, **kwargs):
        self.saveto = saveto
        logging.Handler.__init__(self, *args, **kwargs)

    def emit(self, record):
        if self.saveto is not None:
            self.saveto.append(record)

    handle = emit


def enable_pretty_logging(logger=logging.getLogger()):
    channel = logging.StreamHandler()
    channel.setFormatter(LogFormatter())
    logger.addHandler(channel)
