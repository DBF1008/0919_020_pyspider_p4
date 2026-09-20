#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Created on 2026-09-20

"""
Structured logging helpers with request_id tracing.

`request_id` is generated when a task is dispatched by the scheduler,
carried inside the task dict through fetcher and processor, and bound
to a thread-local context so every log line of one task shares the
same request_id (full-link tracing).
"""

import logging
import threading
import uuid

_context = threading.local()


def new_request_id():
    """Generate a new request id."""
    return uuid.uuid4().hex[:16]


def set_request_id(request_id):
    """Bind request_id to current thread context."""
    _context.request_id = request_id


def get_request_id():
    """Get request_id of current thread context, None if not set."""
    return getattr(_context, 'request_id', None)


def clear_request_id():
    """Clear request_id of current thread context."""
    _context.request_id = None


def ensure_request_id(task):
    """
    Get request_id from task dict, generate and inject one if missing.
    Also bind it to current thread context.
    """
    request_id = task.get('request_id')
    if not request_id:
        request_id = new_request_id()
        task['request_id'] = request_id
    set_request_id(request_id)
    return request_id


def bind_task(task):
    """Bind request_id carried by task to current thread context."""
    return ensure_request_id(task)


def _format_value(value):
    if value is None:
        return '-'
    if isinstance(value, float):
        return '%.4f' % value
    text = str(value)
    if not text:
        return '-'
    if any(c in text for c in ' \t\n"='):
        text = '"%s"' % text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return text


def slog(logger, level, event, **fields):
    """
    Emit one structured log line in logfmt style:

        event=task_done request_id=abc123 project=demo taskid=xxx
    """
    fields.setdefault('request_id', get_request_id())
    parts = ['event=%s' % _format_value(event)]
    for key in sorted(fields):
        parts.append('%s=%s' % (key, _format_value(fields[key])))
    logger.log(level, ' '.join(parts))


def task_log_fields(task):
    """Common structured log fields extracted from a task dict."""
    return {
        'project': task.get('project'),
        'taskid': task.get('taskid'),
        'url': task.get('url'),
    }
