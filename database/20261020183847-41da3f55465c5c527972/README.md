# Codex task worker (opt-in)

Separate service process for the [web task inbox](../20261020183846-e29da0472f9a2be36f01),
running as the same Unix user as the website. No separate account or UID
configuration is required. Disabled unless `NGRAM_CODEX_WORKER_ENABLED=1`.

From the repository root:

```bash
NGRAM_CODEX_WORKER_ENABLED=1 ./database/20261020183847-41da3f55465c5c527972/run-service
```

The default private state/configuration and web node's `.venv` are reused.
If the website uses custom `NGRAM_WEB_CONFIG` or `NGRAM_WEB_STATE` values,
export those same values for the worker. Codex must be installed on PATH and
authenticated for this user. See the web README for sandbox modes, review,
cancellation and timeout behavior. HTTP requests only enqueue tasks.

Successful write tasks copy created and edited node source files into the live
repository through `ngram import-node --overwrite`, preserving IDs, executable
modes, generated outputs and logs. New tasks snapshot current live source files,
including uncommitted nodes, without changing the live Git index or HEAD.
Changes outside node directories remain proposals. Copied-back node links appear
in task details. The worker uses its worktree's Ngram CLI for task commands.
