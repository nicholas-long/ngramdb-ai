# NgramDB Web

Authenticated React + TypeScript dashboard, FastAPI API, safe file previews,
React Flow graph, scheduler activity over SSE, and durable Codex task intake.
This is an ordinary Ngram service node. Node creation uses `ngram create`.

- [Graph and file index](../20261020183847-1fd46fffd14b7dc91e77)
- [Opt-in Codex worker](../20261020183847-41da3f55465c5c527972)
- [Example collector](../20261020184448-ff03b1f0dce6debea036)
- [Example summary](../20261020184448-1807f124753c9185314c)
- [Operations and lessons learned](../20261020181811-69c80a21731282ab1f3e/WEB-OPERATIONS.md)
- [Specification](../20261020181811-69c80a21731282ab1f3e/WEB-UI-SPEC.md)

## Local setup

From the repository root (Python 3.11+ and Node 20.19+/22.12+):

```bash
./initialize-web --origin https://ngram.example.internal
./ngram start
```

For localhost HTTP development use `./initialize-web --local` instead.
The root entrypoint is a symlink to the executable Bash script in this web node.
You can also run `./initialize-web` directly from this directory. Without
`--origin`, first-time setup prompts for the HTTPS origin. The script installs
Python dependencies into this node's `.venv`, builds the client, and prompts
privately for the initial admin password. Use `--username NAME` to choose the
admin name. The repository selector name comes from the Ngram root folder
(for this checkout, `ngramdb-ai`). Existing configuration is preserved; `--reconfigure` updates the
login and origin. `--help` lists all options.

The service stays bound to loopback; server access requires the HTTPS reverse
proxy/VPN described below. Initialization does not install system packages,
start services, or enable the Codex worker. `run-service` starts just the website.
The run/service scripts automatically use this node's `.venv` when present;
`NGRAM_WEB_PYTHON` can override it. `NGRAM_WEB_PORT` defaults to 8765.
If you override `NGRAM_WEB_STATE` or `NGRAM_WEB_CONFIG` during setup, retain the
same settings in the service environment. There are no default credentials.

All npm sources, lockfile, `node_modules`, and intermediate build outputs live
inside **this web node's `client/` directory**. Nothing npm-related is installed
at the repository root. Ngram excludes `node_modules`, `.venv`, Python bytecode,
and pytest caches from checksum, executable, and loose-reference discovery.
The run script hashes frontend sources, uses `npm ci`, checks TypeScript, builds,
and publishes changed assets before atomically replacing `dist/index.html`.
An unchanged build does not rewrite outputs. Old hashed assets remain available
for open browser tabs. Generated dist files and local dependencies are gitignored.

## Private configuration and permissions

`NGRAM_WEB_STATE` defaults to `~/.local/state/ngram-web`. `NGRAM_WEB_CONFIG`
overrides the default `$NGRAM_WEB_STATE/config.json`. Keep both outside
`database/`. Configuration contains:

```json
{
  "origin": "https://ngram.example.internal",
  "secureCookies": true,
  "users": {"alice": {"passwordHash": "<argon2id hash>", "role": "operator"}},
  "repositories": {"ngramdb-ai": "/srv/ngramdb-ai"},
  "taskTimeout": 600
}
```

Use `server.configure` to add users and repositories. Roles are `viewer`,
`operator`, `admin`. Viewers browse the graph and allowed non-secret files;
operators submit/cancel/review their own tasks; admins can inspect all tasks.
Private graph access currently applies to the whole configured repository,
not per-node ACLs. Session IDs rotate on login; sessions expire after eight hours
and are in memory (one Uvicorn worker). Restarting logs everyone out. Role changes
and disabled users take effect immediately. Mutations require session CSRF tokens
and same-origin checks. Login and submission limits are per-process.

For phone access use a private VPN plus an HTTPS reverse proxy. Set `origin`
to the exact HTTPS origin and `secureCookies` to true; omit `--insecure-local`.
Only trust forwarded headers from the local proxy. The web service and Codex
worker can run as the same Unix user, sharing the same private configuration
and state directory. No extra Unix account or shared ACL setup is required.
Keep configuration, task outputs and audit logs inaccessible to other users.
The worker needs access to the allowlisted repositories and writes patch
changes in isolated worktrees. HTTP roles still control task submission
and ownership; Codex sandbox modes still govern task execution.

File access rejects hidden paths, credentials, key files, traversal, symlinks,
and non-regular files. It walks with `openat`/`O_NOFOLLOW` for race resistance.
Previews are capped at 256 KiB; CSV at 101 rows. HTML, SVG and other active content
are downloads, never same-origin executable previews. Markdown disables raw HTML
and external images; browsers escape text. CSP and nosniff apply to responses.

Anonymous publication is explicit: place approved sanitized files beneath
`$NGRAM_WEB_STATE/public/<publication>/<file>`. `/public/<publication>/<file>`
serves those files as attachments using the same path policy. Graph metadata,
logs, task data and arbitrary world-readable files are not public.

## Activity, index, and lifecycle

The CLI's `run-single` emits started/completed JSONL events with full node ID,
run ID, host, process ID, duration and actual program exit code. The scheduler also emits pass completion events. A failing run
returns 1 and still updates dependency hashes after the attempt, so unchanged
inputs are skipped until the cron interval expires or inputs change. Events live outside
the graph in `NGRAM_RUNTIME_DIR` (default `~/.local/state/ngramdb/runtime`).
The server and scheduler must share this location. Event reads are bounded to
the last 2 MiB; rotate/archive the log externally for long-running deployments.
Started events are checked against process liveness on the same host and become
stale on interruption/expiry; older pre-instrumentation runs remain unknown.
PID reuse can make liveness ambiguous; statuses are observations, not durable leases.

`refs` now preserves colons in script lines and handles single executable files,
so JSON/jq expressions do not hide dependencies.

Node discovery invokes `ngram query` once and reads only bounded `README.md`,
`data.json`, and `tags.ngram` metadata. Node lists and details never build graph
relationships or scan output data. File indexing walks allowed filenames and
stat metadata, never file contents. Node, file, dependency and optional loose
indexes have separate locks/caches, so a graph refresh cannot block node lists.

The ordinary graph uses executable dependencies from `ngram refs`. Loose
references are **off by default**, including in the index materializer. The
Graph page has an explicit “Include documentation links” checkbox;
`/api/graph?includeLoose=true` and node refs with `includeLoose=true` opt in.
Opted-in relationships use `ngram docrefs ID`, which scans only Markdown,
skips files over 1 MiB, and reads at most 4 MiB per node. It does not search
JSON/JSONL datasets or generated outputs. Full `ngram query looserefs` remains
available explicitly at the CLI but is never called by the web service.

`source → target` means source depends on or refers to target. Discovery uses
fixed argument arrays, a 15-second cache and bounded CLI timeouts; a timed-out
query terminates its process group so grep/find children cannot keep running.
No HTTP endpoint accepts arbitrary graph query operators or shell strings.
The index node materializes dependency edges and a safe file-stat manifest
outside the graph. Files are opened fresh for previews, so output changes appear
without Python restarts or React builds. Metadata manifests may lag 15 seconds.

Use your supervisor to restart **only the web service** after backend changes;
there is no production auto-reload. With systemd, use the example service in
`deploy/ngram-web.service`, set its paths/user/environment, and run
`systemctl restart ngram-web`. If using `ngram start` to launch the web service,
restart its child separately after code changes. Never run both launchers on
the same port. The core Ngram loop does not have to stop for data/UI updates.

## Agent tasks

HTTP intake only writes validated durable queue files; it never launches Codex.
Task states: queued, running, completed, failed, cancelled, needs-review.
An `Idempotency-Key` header deduplicates retries for the same user and payload.
There are at most 20 queued/running tasks, one worker process at a time, a
configurable timeout (max one hour), cancellation, and a 10 MiB worker log limit.
Task result/event routes enforce ownership, including polling event logs.

The companion worker is disabled by default. Run it as the same user as the
website. From the repository root, in another terminal:

```bash
NGRAM_CODEX_WORKER_ENABLED=1 ./database/20261020183847-41da3f55465c5c527972/run-service
```

With the default setup it uses the same `~/.local/state/ngram-web` state and
configuration and the web node's `.venv` automatically. If you customized
`NGRAM_WEB_CONFIG` or `NGRAM_WEB_STATE` for the website, export the same values
in this terminal. `webUid` and `workerUid` are not required; legacy fields are
ignored. For a persistent deployment set `NGRAM_CODEX_WORKER_ENABLED=1` in the
worker's service environment. Alternatively set it before `./ngram start` to
launch the worker alongside the website. Codex must be installed on PATH and
authenticated for this user.

The installed CLI's
`codex exec --help` was checked for `--json`, `--sandbox`, `-C`, stdin prompts,
`--ignore-user-config`, `--ephemeral`, and `--output-last-message`.

Read-only uses the read-only sandbox and cannot create or update nodes.
Select **Create or update nodes** for write tasks. The worker snapshots the live
tracked and untracked source files into a detached worktree, respecting Git
ignore rules. Uncommitted nodes are included. A temporary Git index records the
snapshot; the live index, branches and HEAD are not modified and no commit is
created. Task diffs use that snapshot as their baseline.

After a successful write task, the worker copies changed `database/<ID>/`
source directories back with `ngram import-node --overwrite SOURCE`. This
creates new nodes and replaces source files in existing nodes. Matching files
are left untouched. Generated data, logs, dependency hashes, caches, and files
absent from the task directory are preserved; copy-back does not delete files.
Changes outside node directories and whole-node deletions remain proposals and
finish as needs-review. Node-only tasks finish as completed. The task page lists
copied-back nodes, including updates to existing IDs.

New node directories are published atomically. Existing source files are
replaced atomically one file at a time. Failed, timed-out and cancelled Codex
runs do not copy files back. If publication itself fails, the task retains any
node IDs already published and reports the error; changes are not rolled back.

For manual recovery, `ngram import-node SOURCE` refuses conflicting existing
files, while `ngram import-node --overwrite SOURCE` explicitly replaces them.
The command rejects symlinks, private key/credential files, and malformed
metadata. It omits runtime state and Git-ignored outputs such as collector
datasets. Imports are bounded to 10,000 source files, 50 MiB per file and
100 MiB total. The scheduler excludes temporary `.ngram-import-*` directories.

The source worktree and full diff remain available for inspection. Collectors
run normally through Ngram after copy-back; task-time network restrictions can
prevent an initial download without preventing source-node creation. Worktrees
and results persist and must be pruned after review. Continuation/resume is not
exposed. Interrupted tasks fail rather than replay automatically. Automated
worker tests use a fake local Codex executable; actual Codex authentication is
provided by the user running the worker.

## Verification

```bash
# In this node
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
npm --prefix client run build
# In the repository root
./run-tests
```

The API exposes `/api/health`, `/api/me`, `/api/nodes`, node detail/refs/files/file,
`/api/files`, `/api/graph`, `/api/activity`, `/api/events` (SSE), and owner-scoped
`/api/tasks` with per-task detail/events/cancel/review. API schemas are version 1.
API docs are disabled on the production HTTP surface. The source tests cover
roles, cookies, CSRF, origin/body bounds, private/anonymous boundaries, traversal,
symlinks, Unix access, previews, live output reads, task idempotency/ownership,
structured failures/stale runs and worker worktree isolation.
