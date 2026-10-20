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
