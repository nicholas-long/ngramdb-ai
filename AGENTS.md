# NgramDB: instructions for coding agents

This file describes the existing NgramDB project, its conventions, and how to work on it safely. It is intended to live at the repository root as `AGENTS.md`.

> Source of truth: repository code and generated documentation, especially `README.md`, `ngram`, the `database/` nodes, and `run-tests`. Updated against this checkout on 2026-10-20. The web dashboard, index and opt-in task worker are implemented; consult their node READMEs and docs/WEB-OPERATIONS.md for current behavior. The original spec also contains future work.

## Purpose and mental model

NgramDB is a **filesystem-backed graph of small data-processing programs**. Each datapoint (node) is a directory identified by a long, timestamp-based ID. Nodes store data, documentation, executable scripts, references to other nodes, and generated artifacts. The graph models dependencies, and NgramDB executes programs when their inputs change, in dependency/topological order. It supports periodic and continuously running nodes.

NgramDB is not a conventional relational database or centralized message broker. Prefer small, composable shell/Python/jq programs and inspectable files over introducing hidden state or heavyweight services. Generated files can be consumed directly by other nodes or a web API. Outputs should be deterministic and stable whenever possible, since changes propagate through the graph.

## Repository layout (verified top level)

```text
ngramdb/
├── ngram                  # CLI entry point
├── database/              # actual node directories and project implementation nodes
│   └── <timestamp-id>/    # examples look like 20240704164832-ae846f0d186e581bb7aa
├── docs/                  # symbolic link to AI Agent documentation directory within ngram database
├── README.md              # generated project documentation, CLI help, examples
├── log.md                 # project log
├── initialize-web         # symlink to web node Bash setup script
├── AGENTS.md              # coding and operational conventions
├── run-tests              # test runner
├── .gitignore
└── LICENSE
```

Nodes typically use a `README.md` for their title and human-readable links, `data.json` for structured metadata, runnable program files, and artifacts/logs. The exact filenames for executable entry points and dependencies vary with the node conventions: **inspect an existing node or CLI implementation before adding one**. Known files/concepts include `stdout.log`, `.ngram.dependencies.hashes`, `.hook-ngram-list`, executable `run-service`, and special tags. Do not assume a proposed `nodes/`, `state/`, or `inbox/` top-level directory exists: the current source tree uses `database/`.

**Create every new node with `./ngram create`**; never invent an ID or create an application directory at the root. Keep Python environments in the owning node and npm manifests, lockfiles, `node_modules` and build intermediates in the web node's `client/`. Do not install npm dependencies at the repository root.

### Links and dependencies

- A node's `README.md` can contain Markdown links to other node directories. `ngram link fromid toid [reason]` creates a **loose** reference via such a Markdown link.
- `ngram forcelink` creates an executable/dependency reference by appending IDs to an appropriate executable data file. Inspect command behavior before invoking it.
- `ngram refs`, `ngram findrefsto`, and the graph query language inspect connections. Distinguish loose documentation relationships from links that affect execution.
- `ngram tsort` sorts IDs by graph dependencies.
- Nodes tagged `ngram-hook-add` have `.hook-ngram-list`, making it possible to rerun discovery logic as new nodes enter the graph.

## CLI guide

Run `./ngram --help` and `./ngram <subcommand> --help` against the checked-out version for authoritative syntax. If installed on PATH, `ngram` is equivalent to `./ngram` in the examples below.

| CLI | Purpose |
| --- | --- |
| `ngram create` | Create node; supports `--title`, `--clone`, `--venv`, `--json` |
| `ngram list` (`ngram ls`) | List datapoints |
| `ngram id` | Generate unique timestamp-based ID, optionally custom date/format |
| `ngram set-title` | Update first README heading and `data.json` title |
| `ngram tag` / `ngram hastag` | Mutate/list tags; test tag membership |
| `ngram link` / `ngram forcelink` | Create loose and executable graph links |
| `ngram refs` / `ngram findrefsto` / `ngram refdata` | Inspect graph references |
| `ngram docrefs ID` | Bounded Markdown-only documentation references; explicit opt-in |
| `ngram import-node [--overwrite] SOURCE` | Copy node source files into the database, preserving generated/runtime files |
| `ngram query` | Query tags, metadata and graph links with an RPN-like language |
| `ngram run` | Run change-driven graph cycles until stable |
| `ngram run-single` | Run single node (inspect help before use) |
| `ngram start` | Run continuously, start services, back off between idle iterations |
| `ngram logs` | Retrieve run logs for a node |
| `ngram tui` | fzf-based interactive development interface |
| `ngram venv` | Enter shell environment with Ngram variables, optional node |
| `ngram checksums` / `ngram dependstats` | Understand file-change and dependency hash tracking |
| `ngram replace` | Atomically replace a generated file from stdin |
| `ngram jsonset` | Set JSON field: `ngram jsonset id field value [-n]` |
| `ngram tsort` | Topologically order node IDs |
| `ngram cleanup` / `ngram clear-logs` | Cleanup; treat as destructive |
| `ngram stubname` / `ngram tagfiles` / `ngram find-executable` | Utilities; inspect implementation |

Typical development:

```bash
./ngram --help
./ngram list
./ngram query @test
./ngram tui
./run-tests
./ngram run       # updates until stable
./ngram start     # long-lived looping mode, includes services
```

`ngram run` may return exit code **1 when no node ran**: do not automatically interpret this as failure in wrappers.

## Tags and scheduling

Documented tags:

- `template`: reusable node template, excluded from normal execution.
- `live`: run at least once per `ngram run` invocation, even if dependencies unchanged. Use only for cheap source/heartbeat checks.
- `service`: with executable `run-service`, launches as a background process under `ngram start`.
- `ngram-internal`: implementation node in the database itself.
- `ngram-hook-add`: responds to newly added nodes via `.hook-ngram-list`.
- `test`: test node.
- `cron-daily`: approximately 24h periodic reruns.
- `cron-hourly`: approximately 60m periodic reruns.
- `cron-five`: approximately 5m periodic reruns.

Tag notation when querying uses `@`, e.g. `@cron-hourly`. Verify current scheduler behavior and applicable test coverage when changing timing or cooldown logic. A task which stays alive belongs in a `service` node; a short polling collector usually belongs in `live` or a cron-tagged node.

A completed `run-single` attempt **must write `.ngram.dependencies.hashes` even when the program fails**. Stage hashes outside the node and publish after execution so generated files are reflected. Return codes are 0 for success, 1 for failure, 2 for unchanged/skipped, and 3 for successful live execution. Before this fix, failed collectors retried every scheduler pass because their hashes never updated.

Cron tags expire recorded hashes during scheduler passes; they are interval-based, not wall-clock OS cron jobs. Changed inputs can still trigger earlier runs. `run-single` alone does not expire cron hashes or force unchanged nodes to run. Preserve the previous valid output on collector failures; a failure must not become an immediate retry loop.

## Graph query language: use it in scripts

**Yes, prefer `ngram query` to writing ad-hoc filesystem parsers for selecting graph nodes.** The CLI already understands tags, links and node metadata. The documented evaluator is reverse-Polish/set-oriented: start from the set of IDs, apply filters and traversals left-to-right, and emit matching IDs unless an output operator changes the representation.

Documented operators:

| Operator | Meaning |
| --- | --- |
| `@tag` | Restrict working set by tag |
| `refs` | Traverse outward references from current nodes |
| `looserefs` | Traverse loose references without requiring executable linkage |
| `refstype` | Traverse references matching a string/link type; check exact arguments in code |
| `refsto` | Reverse traversal, nodes pointing to current set |
| `id <ID>` | Set working set to one node |
| `not ...` | Set difference against subsequent tag/file/subcommand argument |
| `union ...` | Set union |
| `filterjson ...` | Filter `data.json` with jq-style `select` expression; verify quoting |
| `json` | Combine selected nodes' `data.json` into a JSON array |
| `jsonset ...` | Set JSON metadata fields (mutating; avoid in read-only scripts) |
| `expand` | Expand references into a functional group |
| `dirs` | Return directory paths, suitable for shell iteration |

Repository README examples (use as behavioral guide):

```bash
# Nodes with the test tag
ngram query @test

# Human-readable labels/titles (option shown in README examples)
ngram query --human @test

# Nodes referenced by nodes tagged logdata and web
ngram query @logdata @web refs

# Keep only the references that are tagged csvfile
ngram query @logdata @web refs @csvfile

# Walk a more complicated graph, excluding cats
ngram query @me refs @person @family refs @pet not @cat

# Bash process substitution permits a nested query
ngram query @pet not <(./ngram query @me refs @person @family refs @pet not @cat)

# Directory paths for processing
ngram query @test dirs

# Structured metadata where supported by the checkout
ngram query @test json | jq '.'
```

**Shell scripting advice:**

- Use `ngram query ... dirs` to get canonical paths instead of guessing the long IDs or scanning folder names manually.
- Quote node IDs and file paths. Use `while IFS= read -r path; do ...; done` rather than `for x in $(...)` for paths with spaces.
- For user-facing search or a web graph, derive node IDs and topology from CLI/query results, then enrich with README, `data.json`, file metadata and logs. Cache or materialize outputs in a dedicated node if expensive.
- Treat CLI query arguments as a language, not shell: in HTTP endpoints use subprocess argument arrays and a restricted query grammar. Never interpolate arbitrary prompts into a shell command.
- Confirm available operators and exact syntax against `database/*/subcommands/query` and current `--help` before adding tests or deploying scripts.

## Generated-file and program conventions

- A node program can generate multiple files and logs. Recent stdout is stored in `stdout.log`; stdout and stderr are logged during executions.
- **Stable outputs are crucial.** If stdout or a generated file changes unnecessarily (timestamps, unordered maps, inconsistent formatting), downstream nodes can rerun repeatedly.
- Use `ngram replace <file>` to publish completed output rather than truncating the destination while writing. Example: `jq -S . input.json | ngram replace output.json`. Check the command's exit behavior.
- Avoid rewrite-on-every-loop for static outputs; compare or hash content and publish only meaningful changes. This includes React build artifacts and dashboard manifest files.
- Honor `.ngram.dependencies.hashes` and existing dependency detection logic. Do not indiscriminately delete internal hashes or logs.
- Keep Node output formats versioned or compatible because dependent scripts may parse them.
- Prefer small Bash/jq/Python programs for data collection/transformation, including periodic AWS/API syncs. Isolate credentials and don't publish secrets or sensitive raw results.

## Service and web dashboard work

The implemented services are:

| Node | ID |
| --- | --- |
| Web API and React client | `20261020183846-e29da0472f9a2be36f01` |
| Graph/file index | `20261020183847-1fd46fffd14b7dc91e77` |
| Opt-in Codex worker | `20261020183847-41da3f55465c5c527972` |
| Daily CVE collector | `20261020201233-ac997a4a21deafd6edfb` |

Use [the operations guide](docs/WEB-OPERATIONS.md) for actual checkout paths, setup and troubleshooting, and the web node README for API/security details. `docs/WEB-UI-SPEC.md` records design intent; do not treat every proposed feature as shipped.

Key architecture principles:

1. NgramDB stays responsible for the graph and updates. The web server does not become a second graph engine.
2. Prefer APIs that read validated materialized files on demand; data changes should **not** restart the server.
3. The web client should be built as part of the web node's **run script** when sources/dependencies change. A separate executable `run-service` can run the persistent Python server.
4. For code changes use a controlled restart (systemd or service supervisor) or a service-node mechanism verified in the checkout. Do not use development auto-reload for production.
5. Keep public file publishing separate from private filesystem access and HTTP authorization. Unix permissions constrain the web *process*, not individual HTTP visitors.
6. The web server must not execute arbitrary shells or Codex prompts directly from HTTP requests. Enqueue authenticated, validated tasks and let a separate worker process them.

### Task execution and publication

- Web and worker run as the **same Unix user**. No extra account or UID configuration is required. HTTP intake only enqueues tasks; the worker is a separate process enabled by `NGRAM_CODEX_WORKER_ENABLED=1`.
- Configuration and private state default to `~/.local/state/ngram-web/config.json` and `~/.local/state/ngram-web`. “Shared state” means this filesystem directory used by both processes, not a separate service. Custom state/config environment values must match.
- Derive repository labels from the Ngram root directory or Git information, never hardcode `ngramdb`. This checkout is `ngramdb-ai`.
- Write tasks snapshot live tracked and untracked source using a temporary Git index, respecting ignore rules. Do not modify the live Git index, HEAD or branches to create task baselines.
- Successful write tasks publish new and edited node sources via `ngram import-node --overwrite`. Existing matching filenames are replaced; missing files, generated datasets, logs and hashes are preserved. New directories publish atomically; existing files replace atomically individually. Publication can partially succeed and is not rolled back.
- Failed/cancelled/timed-out execution does not publish. Changes outside node directories and whole-node deletions remain proposals (`needs-review`). Read-only tasks cannot publish. Keep results/worktrees for review; cleanup is manual.

### Large datasets and optional references

Loose references are low priority and **off by default**. Never make node listing, details or file search wait for loose-reference discovery, graph construction or dataset reads. Node metadata, file stats, dependency edges and optional documentation edges have independent locks/caches. Discover IDs with the CLI, read bounded metadata, and index filenames/stat information without reading output contents.

The old web path called `query looserefs` and recursively scanned a CVE dataset exceeding 1.5 GB to list only about 30 nodes. Use `ngram refs` for executable dependencies. Explicit `includeLoose=true` requests use `ngram docrefs`, limited to Markdown files of at most 1 MiB and 4 MiB per node. Kill the entire subprocess group on CLI timeout, including grep/find descendants. Do not put full-content `looserefs` on an ordinary HTTP request path.

The CVE collector accepts direct baseline ZIPs and `.zip.zip` wrappers containing `cves.zip`; stage the inner ZIP on disk and never extract archive paths. Current output is deterministic `cves.jsonl.gz`, streamed and atomically published only on change. Keep downloads and large outputs ignored by Git and out of task imports. See its node README and tests for format details.

## Development protocol for agents

1. Read local `README.md`, inspect applicable `database/` nodes, and run CLI `--help` before implementing something new. This project is self-documenting, with CLI implementations inside the graph.
2. Find an existing similar template or service and follow its actual executable naming, graph dependency format, and log conventions.
3. Implement the smallest change that preserves the filesystem model.
4. Run `./run-tests` and any relevant node-specific checks; don't assume a clean run without executing tests.
5. When changing scripts that drive the scheduler or CLI, confirm compatibility with `ngram run`, `ngram start`, service lifecycle, tags and queries.
6. Avoid adding heavy persistent infrastructure unless essential. Do not require a SQL server or hosted cloud service for the dashboard.
7. Do not commit secrets, generated credentials, private task prompts, or large runtime datasets. Keep templates and examples safe to version-control.
8. Document new CLI flags, tags, node formats and permissions, and update relevant generated documentation through its actual generation mechanism.

## Documentation maintenance

Update node READMEs and the documentation node rather than manually patching generated root documentation. The root README generator is `database/20240709053001-b005119b744456da6cc0/run`; regenerate with `./ngram run-single 20240709053001-b005119b744456da6cc0`. Do not run the entire scheduler just to regenerate docs, and do not start/restart persistent services for documentation-only work. Stop only the identified service when asked; verify its listener is gone and avoid killing unrelated scheduler/worker processes.

For web code checks, run pytest from the web node with its development requirements installed, build with `npm --prefix client run build`, and run `./run-tests` at the root. Documentation-only edits need link/content validation, not service startup or dependency installation. Never report a check as passed without running it.
