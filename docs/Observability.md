Observability
=============

pyspider components (scheduler / fetcher / processor) provide structured
logging, request_id tracing, unified metrics and health check endpoints.

Structured Logging & request_id
-------------------------------

Key lifecycle events are logged in logfmt style (`key=value` pairs):

    event=task_selected request_id=40f102c3153c47f6 project=demo taskid=abc url=http://example.com/
    event=fetch_done request_id=40f102c3153c47f6 status_code=200 latency=0.1230
    event=process_done request_id=40f102c3153c47f6 latency=0.0450 follows=2

A `request_id` is generated when the scheduler dispatches a task
(`on_select_task`), carried inside the task dict through fetcher and
processor, and returned in the status pack, so every log line of one task
shares the same `request_id` (full-link tracing).

Metrics
-------

Each component collects unified metrics (task rate, success/failure,
latency distribution) and exposes them in Prometheus text exposition
format via a built-in HTTP server:

- `GET /metrics` - Prometheus format metrics
- `GET /health`  - JSON health check (`status`, `component`, `uptime`, ...)

Default ports (can be changed / disabled with `--metrics-host` /
`--metrics-port`, `0` disables the server):

| component | metrics port |
|-----------|--------------|
| scheduler | 23334        |
| fetcher   | 24445        |
| processor | 24446        |

Exposed series:

- `scheduler_tasks_total{project, status}` - tasks by status
  (pending/selected/success/failed/retry)
- `scheduler_task_duration_seconds{project, stage}` - fetch/process latency histogram
- `scheduler_inqueue_tasks` - tasks waiting in project queues
- `fetcher_requests_total{project, type, status}` - fetch results
- `fetcher_request_duration_seconds{project}` - fetch latency histogram
- `fetcher_inflight_requests` - in-flight fetch requests
- `processor_tasks_total{project, status}` - processed tasks by result
- `processor_task_duration_seconds{project}` - process latency histogram

Graceful Shutdown
-----------------

All components handle `SIGTERM`/`SIGINT` gracefully: after receiving the
signal, the component stops accepting new tasks, waits for the current
task (scheduler loop step, processor task) or in-flight fetches (fetcher,
up to 30s) to finish, then exits. `pyspider all` forwards the signal to
components running in subprocesses.
