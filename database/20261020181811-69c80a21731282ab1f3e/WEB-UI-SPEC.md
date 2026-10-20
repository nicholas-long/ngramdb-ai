# NgramDB Web: filesystem-first dashboard and agent control plane

Status: **design specification with an implemented initial release** (2026-10-20). See [operations and verified behavior](WEB-OPERATIONS.md) and the [web node README](../20261020183846-e29da0472f9a2be36f01/README.md) for what is shipped. Remaining proposals below are not promises of implemented features. Current behavior takes precedence: same Unix user for web/worker, automatic successful node-source copy-back, and loose references disabled by default.

## Objective

Build an authenticated, mobile-friendly web interface for exploring NgramDB nodes, files, dependencies, outputs and execution history. Use **React + TypeScript** on the frontend, **Python + FastAPI** on the backend, and keep NgramDB's filesystem as the source of truth. The UI should serve mostly static/materialized files and live information from logs. It should also allow an authenticated user to submit Codex prompts from a phone; those requests become durable NgramDB tasks processed by a separate worker.

The web application itself should be an NgramDB **service node**, not a new top-level application framework or replacement scheduler.

## Non-goals (initial release)

- No centralized relational DB requirement; no cloud dependency.
- No unrestricted remote shell or direct unauthenticated Codex execution.
- No complete graphical DAG editor or generalized permissions management system in MVP.
- No filesystem mutations through public browsing routes.

## Verified existing NgramDB integration points

- Real node directories live in `database/<long-id>/`, with README, metadata, scripts, files and logs.
- `ngram query`, `ngram refs`, `ngram logs`, and related CLI commands expose graph state.
- `ngram start` loops and starts nodes tagged `service` with executable `run-service`.
- A normal `ngram run` propagates file changes; `template`, `live`, cron tags and `ngram-hook-add` affect scheduling.
- `ngram replace` is the recommended way to safely publish new generated data.

Inspect real service and build nodes locally before copying conventions. The layouts below are **illustrative desired files inside one actual `database/<WEB_NODE_ID>/`**, not claims about existing directory conventions.

## Proposed node structure

```text
database/<WEB_NODE_ID>/
├── README.md                   # description and graph links
├── data.json                   # Ngram metadata, discoverable via CLI
├── run                         # executable: install/build frontend and generate manifest if needed
├── run-service                 # executable: starts long-running Python API
├── requirements.txt            # pinned Python dependencies
├── server/
│   ├── app.py                  # FastAPI entrypoint
│   ├── auth.py                 # authentication and session handling
│   ├── graph.py                # safe ngram CLI adapter
│   ├── files.py                # allowlisted file browsing
│   ├── events.py               # activity tailer/SSE
│   └── tasks.py                # validated task enqueueing
├── client/
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── src/                    # React/TypeScript SPA
├── dist/                       # build output (generated, not source of truth)
└── .build-hash                 # optional input digest / build cache
```

Make executables and dependency linkage match verified NgramDB conventions. A companion `codex-worker` node should be a **separate node and process**, running as the same Unix user as the website. A possible third node materializes graph/index JSON for fast browsing and can carry `ngram-hook-add` to refresh on newly created nodes. Start small: materialization can initially be done by the web node or on demand.

## Runtime architecture

```text
Ngram source nodes (AWS, CVEs, APIs, jq, Python)
     | update stable files, stdout logs, node outputs
     v
Ngram run/start scheduler -----> graph-index materializer (JSON snapshots)
     |                                     |
     +---- web service node -----> FastAPI file/graph APIs
     |                                     |
     |                              React SPA on phone
     |                                     |
     +---- task inbox <--- validated authenticated POST task
     |          |
     +---- Codex worker (same Unix user, separate process) --> task log, results, diffs
```

Data changes: serve changed files on next request and optionally push `file_changed`/`node_finished` SSE updates; do **not** rebuild frontend or restart the Python service. Frontend source changes: rebuild Vite assets through the web node `run` script; service can serve updated hashed assets without restarting. Python backend code changes: restart the service via a supervisor (or verified Ngram service mechanism). Avoid making server reloads depend on every data file write.

## Frontend stack

- Vite, React, TypeScript, React Router, TanStack Query for API caching/refetching.
- React Flow (`@xyflow/react`) for pan/zoom dependency graph and active-node highlighting.
- Markdown renderer such as `react-markdown` with `remark-gfm`; sanitize / disable raw HTML unless using a rigorously sanitized HTML pipeline. Give Markdown links to local node docs internal navigation destinations.
- Tailwind CSS or a small accessible CSS system; mobile-first responsive layout.
- Prefer EventSource / Server-Sent Events for one-way runtime events. Native WebSocket works for more interactive bidirectional features; Socket.IO is optional, not a dependency for MVP.
- Deliver built JS/CSS via FastAPI static serving or reverse proxy; no Vite development server exposed in production.

## Core pages

### 1. Dashboard (`/`)

Show latest run activity, running/failed/stale nodes, counts, last successful scheduler pass, and quick search. Show a recent activity stream with status, node title, ID, duration and time. Provide a prominent **Submit task** affordance only to authenticated users with task permissions.

### 2. Graph (`/graph`)

Use a graph JSON snapshot built from `ngram query`, `refs`/`refdata` and documented relationship types. Nodes are identified by **full stable IDs**, displayed with short labels and README titles. Keep the full ID in tooltip/detail panel. Executable dependencies are the default graph. Loose Markdown links are optional and disabled by default; explicit opt-in uses bounded Markdown-only discovery, never dataset content scans. Highlight running nodes; green completed, amber queued/stale, red failed. Animation should be subtle and optional for reduced-motion users. Click opens node detail; pan/zoom, filter by tags/type, search, and focus upstream/downstream neighborhood. Activity arrives via SSE; don't redraw the whole graph for every log line.

### 3. Node browser (`/nodes`, `/nodes/:id`)

Node listing supports full ID/title/tag search, sorting, filters and pagination. Each row/card provides **Copy ID**, **Copy Link**, last run, tags and state. Node details: display full ID and copy actions, README rendered as HTML, metadata (`data.json`), tags, dependencies, referencing nodes, execution history, output files, and logs. Other `.md` files can be selected and rendered in a Markdown document viewer; do not assume only README is documentation.

**Node URLs must use immutable IDs**, for example `/nodes/20240704164832-ae846f0d186e581bb7aa`. Copy Link uses the actual current browser origin plus this canonical path. Link targets should survive title changes. Optional deep link to node file `/nodes/:id/files/<encoded relative path>`.

### 4. Files (`/files`, `/nodes/:id/files/*`)

Provide directory browsing within an allowed node root; breadcrumb navigation, filename, extension/MIME, size, modified timestamp, content-type filters, sort, preview/download, and Copy Link. Global search can find **data files by extension or type** (`.json`, `.jsonl`, `.csv`, `.md`, `.txt`, images, etc.), filename glob, tags and optionally bounded content search. Filters distinguish generated outputs, scripts, docs, logs, and metadata when detectable. Default to an indexed file manifest for cross-node search, refreshed by scheduled node or file-change events. Allow filename/type search in MVP; add content indexing later.

Preview rules: `.md` -> sanitized HTML; `.json` -> formatted/expandable tree and raw download; `.csv` -> bounded paginated table; text -> escaped, line-numbered viewer; images -> safe inline image; binary -> download only. Cap preview size and return a truncated indicator, streaming/download for large files. Never automatically render arbitrary HTML/JS output as same-origin executable content.

### 5. Activity and logs (`/activity`)

Show real scheduler/worker events, not invented status. Per node: timestamps, duration, exit code, stdout/stderr links, run ID where available, and relevant input/output changes. Start with a bounded polling parser if logs are not structured. Prefer later emitting append-only JSONL events from scheduler instrumentation:

```json
{"timestamp":"2026-10-20T08:00:00Z","runId":"example-run","nodeId":"example-id","kind":"started"}
{"timestamp":"2026-10-20T08:00:04Z","runId":"example-run","nodeId":"example-id","kind":"completed","exitCode":0}
```

These are **proposed event fields**; implement/test logging before making them a UI contract. Never claim to know if a node is still running based solely on an old `started` line. Include process/lease state and stale detection.

### 6. Agent tasks (`/tasks`, `/tasks/:id`)

Authenticated owner/admin can submit a prompt, select an allowlisted repository/working directory, choose read-only or Create or update nodes mode and inspect event streams, logs, results and diffs. Successful write tasks automatically import new and edited node source files with overwrite; changes outside nodes remain proposals for review. Create durable task files with unique IDs, status transitions and run output; a dedicated worker uses `codex exec --json` or supported SDK and persists outputs. Expose no arbitrary CLI flags and avoid shell string interpolation. Support continuation/resume only after testing Codex command syntax and session lifetime. Support cancel/timeout and idempotent task intake. Distinguish queued, running, completed, failed, cancelled and needs-review states.

## Basic API contract (v1 proposal)

```text
GET  /api/health
GET  /api/me
GET  /api/nodes?q=&tag=&limit=&cursor=
GET  /api/nodes/{id}
GET  /api/nodes/{id}/refs
GET  /api/nodes/{id}/files?path=
GET  /api/nodes/{id}/file?path=                 # raw download or safe preview response
GET  /api/files?extension=&mime=&q=&limit=&cursor=
GET  /api/graph?tag=&focus=&depth=
GET  /api/activity?nodeId=&after=&limit=
GET  /api/events                                 # SSE event stream
GET  /api/tasks                                  # authenticated
POST /api/tasks                                  # authenticated + CSRF protection
GET  /api/tasks/{id}                             # authenticated
GET  /api/tasks/{id}/events                      # authenticated
POST /api/tasks/{id}/cancel                      # authenticated
```

Proposed node JSON response:

```json
{
  "id": "20240704164832-ae846f0d186e581bb7aa",
  "title": "Example node",
  "tags": ["example"],
  "links": {"self": "/nodes/20240704164832-ae846f0d186e581bb7aa"},
  "files": [{"path": "README.md", "kind": "markdown"}],
  "lastRun": null,
  "status": "unknown"
}
```

The server should implement graph discovery by invoking the existing CLI via `subprocess.run([...])` with bounded timeouts and parsed outputs, or by consuming dedicated CLI-generated snapshots. Avoid duplicating undocumented graph semantics with independent file parsing. Use short-lived caching with invalidation for large graphs.

## Authentication and authorization

**Initial recommended deployment:** bind FastAPI to `127.0.0.1`, expose through Tailscale/private VPN and HTTPS reverse proxy. For phone access, use Tailscale identity/network policy plus app-level login for privileged actions. As a simple self-hosted app login, use Argon2id password hashes and server-side sessions in Secure + HttpOnly + SameSite cookies, rotating session IDs on login, with CSRF protection for mutations. Rate-limit login/task submissions. Do not write credentials into the graph or public output files.

Roles for MVP:

- **Anonymous**: only specifically published public static files and public API endpoints.
- **Viewer**: read allowed internal graph/node metadata and non-secret files, no task creation.
- **Operator/Admin**: browse permitted resources, submit/cancel agent tasks, see private task results and audit actions.

Unix DAC is a second enforcement layer. `chmod 644` means a *local process* can read a file, **not** that HTTP clients have authorization to it. Do not expose all world-readable files automatically; use an explicit public export root/manifest and separate private namespaces. Restrict the web process to read-only access to graph data plus write-only access to a dedicated task inbox. The supported deployment runs the web service and Codex worker as the same Unix user; a separate Unix account is not required. Apply HTTP authorization and Codex sandbox restrictions. Never serve `.env`, credentials, internal `.git`, private keys, session secrets or authentication data. Put explicit deny rules in tests.

Normalize node IDs and relative paths, reject path traversal/NUL bytes, ensure canonical resolved path stays beneath an allowed root, and treat symlinks as untrusted (default deny for download/preview; or resolve and check both root and target). Beware that Markdown links/images, raw HTML and content from nodes may be untrusted and could lead to XSS or SSRF. Disable server-side fetching of arbitrary URLs from Markdown. Serve potentially active files as attachment, and set Content-Security-Policy and `X-Content-Type-Options: nosniff`.

## Filesystem-facing publication model

- Collectors write to their own node directories; processing nodes consume that data and publish stable artifacts.
- A public publication node explicitly copies/links only approved sanitized data into a dedicated served tree, atomically updating files. The public API reads only that tree.
- Private browsing API can operate over `database/<id>` using a minimally privileged account, authentication and explicit allow/deny rules.
- Do not count mere file mtime changes as new semantic data. Prefer checksum/content comparison and use `ngram replace` where appropriate.
- Build an index/manifest JSON for fast cross-node file search; refresh after dependency changes or on a chosen cron tag.

## Frontend build and service lifecycle

The web node **run script is responsible for rebuilding React**. It should check Node/npm availability, use a committed lockfile and deterministic `npm ci`, run TypeScript checks and `npm run build`, then publish `dist/` only after a successful build. Avoid unnecessary rewrites. In development, running `npm run dev` independently is fine, but the production service must serve the compiled `dist` generated by Ngram.

Illustrative script sketch (adapt to verified Ngram entrypoint conventions):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
cd client
npm ci
npm run build
# Publish to ../dist atomically, only after successful build.
# Avoid changing output content if inputs haven't changed.
```

The service process runs FastAPI, e.g. `python -m uvicorn server.app:app --host 127.0.0.1 --port 8765`. Use systemd or a verified Ngram service mechanism for restart on backend code changes. Watching all node output directories for reload is a bug: those files should be read live without process restarts. Avoid a dependency cycle in which UI build files trigger their own rebuild endlessly.

## Initial working nodes / milestone plan

**Milestone 1: Read-only local UI**

1. Inspect repository service examples and `ngram` CLI; identify real execution file conventions.
2. Create web service node; working FastAPI `/api/health`, SPA served from built Vite output, and `README.md` describing its execution.
3. Implement `GET /api/nodes`, `GET /api/nodes/{id}`, `GET /api/nodes/{id}/files` using existing CLI/verified filesystem; full-ID routing and Copy ID/Copy Link in UI.
4. Render README and other `.md` safely. Add JSON/CSV/text previews and downloads with size/path limits.
5. Implement search by title, full ID, tag, extension/type and filename.

**Milestone 2: Graph and live activity**

6. Add a graph index/materializer node and file manifest, preferably tagged `ngram-hook-add`; export stable JSON using existing graph query/refs CLI.
7. Add React Flow view, clickable nodes, link-copy, dependency highlighting and per-node status.
8. Instrument reliable structured run events or build a bounded log adapter; broadcast via SSE.

**Milestone 3: Authentication and publication**

9. Add login sessions, CSRF, rate limits, audit log, public export tree, private browsing, permission checks and hardened file serving.
10. Test remotely over VPN/HTTPS; no privileged endpoint publicly accessible by default.

**Milestone 4: Codex task workflow**

11. Create task inbox and separate Codex worker node; validate schema, permissions, allowed repositories and concurrency limits.
12. Add phone-friendly prompt form, per-task streaming logs, results and review/approval screen; implement cancellations/timeouts.
13. Add example periodic collector (mock or safely credentialed AWS/API) generating JSON, a tiny jq transformation node, and dashboard cards that show its outputs.

## Example smoke tests / acceptance criteria

- `./run-tests` passes and no pre-existing CLI behavior breaks.
- `ngram start` can run the web service, and `ngram run` can rebuild React when frontend source changes without continuous rebuild cycles.
- Changes in collector `output.json` become visible from the API without restarting Python.
- Full ID URLs, Copy ID and Copy Link work on desktop and phone.
- Node Markdown links navigate correctly; HTML/script injection cannot execute.
- Browsing any `../`, encoded traversal or symlink escape path fails safely.
- Anonymous clients cannot read private files, logs or task submissions; Linux permission tests and HTTP authorization tests both pass.
- File filters find `.json`, `.csv`, `.md` across nodes; file previews are bounded.
- Graph shows correctly directed edges and reflects actual run events, including a failed run.
- Only authenticated authorized users can enqueue Codex tasks, and task events are not visible across authorization boundaries.
- Backend restart upon backend source changes does not kill/interrupt the entire Ngram scheduler.

## Open design questions for future iteration

- Exact graph file/index contract and whether existing CLI offers machine-readable JSON for references, statuses and logs.
- How scheduler logs are structured today and which stable events need new instrumentation.
- Whether Node scripts and Vite outputs should live in one Ngram node or use separate web-build and web-service nodes for cleaner dependency invalidation.
- Whether anonymous public export is needed on the same hostname as authenticated private browsing or should use a distinct port/service account.
- Whether eventual edit operations create or mutate nodes directly through the CLI, and what approval/audit workflow should govern them.

These are implementation questions; do not silently invent existing CLI semantics or skip security boundaries.
