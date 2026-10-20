# Web operations and lessons from this checkout

Verified against local source on 2026-10-20. The [web README](../20261020183846-e29da0472f9a2be36f01/README.md) describes API/security details; the [original specification](WEB-UI-SPEC.md) includes future design work.

## Actual paths

These are this checkout's paths, not suggested server locations. On another server, use its repository root and the home directory of the user running the services.

| Purpose | Path in this checkout |
| --- | --- |
| Ngram root / repository named `ngramdb-ai` | `/Users/coyote/ngramdb-ai` |
| Web node | `/Users/coyote/ngramdb-ai/database/20261020183846-e29da0472f9a2be36f01` |
| Worker node | `/Users/coyote/ngramdb-ai/database/20261020183847-41da3f55465c5c527972` |
| Index node | `/Users/coyote/ngramdb-ai/database/20261020183847-1fd46fffd14b7dc91e77` |
| Private configuration (default) | `/Users/coyote/.local/state/ngram-web/config.json` |
| Private state (default) | `/Users/coyote/.local/state/ngram-web` |
| Task queue/status files | `/Users/coyote/.local/state/ngram-web/tasks/` |
| Task output and diffs | `/Users/coyote/.local/state/ngram-web/task-output/` |
| Isolated task worktrees | `/Users/coyote/.local/state/ngram-web/worktrees/` |
| Scheduler events (default runtime directory) | `/Users/coyote/.local/state/ngramdb/runtime/` |

“Shared state” means the same private directory used by the website and worker. It is ordinary filesystem state, not a database server or an additional account. Keep it outside `database/`. Do not publish configuration, task prompts, logs or credentials.

## Initialize and run

From the root, `./initialize-web --local` sets up localhost HTTP. For a server, run `./initialize-web --origin YOUR_HTTPS_ORIGIN` with your actual externally accessed origin. The same Bash script works from the web directory. It installs Python dependencies in the web node's `.venv`, builds the client, and prompts for an admin password. It preserves existing configuration unless `--reconfigure` is supplied. It does not start services or enable Codex tasks.

All npm manifests, lockfiles, `node_modules` and intermediate build files belong in the web node's `client/`. Create new nodes with `./ngram create`; do not create a separate root-level web application or invent node IDs.

To run only the website from the repository root:

```bash
./database/20261020183846-e29da0472f9a2be36f01/run-service
```

It listens on `127.0.0.1:8765` by default. Use an HTTPS reverse proxy/VPN for server access. `./ngram start` can launch service nodes alongside the scheduler instead; do not launch a second website on the same port. Restart only the web service after backend changes. Data changes do not need a restart; restarting clears login sessions.

## Enable tasks

Run the worker as the same Unix user as the website, with Codex installed on PATH and authenticated for that user. From the root, in a separate terminal:

```bash
NGRAM_CODEX_WORKER_ENABLED=1 ./database/20261020183847-41da3f55465c5c527972/run-service
```

For persistent deployment put this setting in the worker supervisor's environment. No separate Unix user, `webUid`, or `workerUid` is required. The tasks page's repository name is derived from the root folder (`ngramdb-ai` here); the configured repository must point at the real root.

| Setting | Where / meaning |
| --- | --- |
| Users, roles, origin, repositories, task timeout | Private `config.json`; initialization creates it, `server.configure` manages users/repositories |
| `NGRAM_CODEX_WORKER_ENABLED=1` | Worker process environment; opt-in switch |
| `NGRAM_WEB_STATE` | Optional private state directory override; identical for website, worker and index |
| `NGRAM_WEB_CONFIG` | Optional config-file override; identical for website and worker |
| `NGRAM_WEB_PYTHON` | Optional Python executable override; service scripts use the web `.venv` automatically |
| `NGRAM_WEB_PORT` | Website listener port; default 8765 |
| `NGRAM_RUNTIME_DIR` | Scheduler event directory; website and scheduler must agree |

Select **Create or update nodes** for tasks that must publish nodes. Read-only tasks cannot write. The worker snapshots tracked and untracked live source into an isolated worktree, respecting Git ignore rules, using a temporary index without changing the live index or HEAD.

Successful write tasks automatically copy new and edited `database/<ID>/` source files back through `ngram import-node --overwrite`. Existing files with matching names are overwritten, matching contents are untouched, and extra existing files are retained. Generated outputs, caches, logs and dependency hashes are preserved. Outside-node changes and whole-node deletions remain proposals (`needs-review`). Failed, timed-out and cancelled executions do not publish. Publication is atomic for new directories and per existing file, not a transaction over all nodes; a publication failure can leave some nodes imported and reports their IDs.

For a node-ID conflict during manual recovery, inspect the source and use `./ngram import-node --overwrite SOURCE_DIRECTORY` to explicitly allow replacement. Worktrees/results remain for review and require manual cleanup. Collectors execute through Ngram after publication; creating source files does not imply the first network download succeeded.

## Scheduling and collector failures

`cron-daily` means approximately 24 hours between scheduled attempts while the scheduler is running, not an OS cron job or a promise of midnight execution. Input changes can trigger earlier execution. `run-single` checks hashes but does not itself expire cron intervals.

Every completed attempt must update `.ngram.dependencies.hashes`, including a failed attempt. The previous failure path skipped this write, so the scheduler saw stale conditions and repeatedly retried the CVE collector, exhausting GitHub rate limits. The corrected path records hashes after execution and still returns failure. Unchanged nodes then skip until inputs change or the scheduler expires the interval. Exit codes: success 0, failure 1, unchanged skip 2, successful live execution 3. `ngram run` can return 1 when no node ran.

The [CVE collector](../20261020201233-ac997a4a21deafd6edfb/README.md) accepts both direct baseline ZIPs and `.zip.zip` artifacts containing an inner `cves.zip`. Treating a wrapper as the CVE record archive caused “Baseline contains no CVE records.” Current output is streamed, deterministic `cves.jsonl.gz`; validation/download failures preserve the last good snapshot. GitHub discovery and downloads still require network access and may be rate-limited. Compression reduces disk use; it does not make recursive content scans appropriate.

## Node-list performance

The old node-list path built the graph and searched loose references through a CVE dataset exceeding 1.5 GB. This made a list of roughly 30 nodes take minutes. Node discovery now uses one CLI query and bounded README, data and tag metadata. File search reads filenames/stat information. These indexes have separate locks/caches from dependency and documentation edges.

Loose references are low priority and off by default. The Graph page's **Include documentation links** checkbox and `includeLoose=true` API option explicitly request them. `ngram docrefs` reads Markdown only, capped at 1 MiB per file and 4 MiB per node. Full `ngram query looserefs` remains an explicit CLI operation, never a normal web request. CLI timeouts kill process groups so child scans do not continue in the background.

A local measurement after the fix returned 33 nodes in about 34 ms cold and 7 ms cached; these are observations, not performance guarantees. Preserve dataset-size regression tests and the separation of metadata/file/dependency/loose indexes.

## Stop and verify

For a foreground service use Ctrl-C. For supervised services stop the corresponding supervisor unit. For a detached server, identify its listening PID with `lsof -nP -iTCP:8765 -sTCP:LISTEN`, verify its command and working directory belong to this web node, then send SIGTERM to that PID. Verify the listener is gone. Avoid broad Python/Uvicorn kill commands that could stop unrelated programs. A supervisor may restart a killed child, so stop its unit when applicable. Do not stop the scheduler or worker merely to stop the website.

The detached development website was explicitly stopped on 2026-10-20. This records that action, not a guarantee about current process state; initialization and documentation updates do not start it.
