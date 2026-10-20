# MITRE CVE List: daily official CVE JSON snapshot

Downloads the complete daily baseline from the [official CVE List](https://github.com/CVEProject/cvelistV5), the CVE Program's current distribution source (replacing legacy MITRE CSV/XML downloads).

Tagged `cron-daily`: NgramDB reruns it after roughly 24 hours while `ngram start` is running, or on the next `ngram run` after that interval. This tag does not install an operating-system cron job.

Run manually with `./ngram run-single 20261020201233-ac997a4a21deafd6edfb` from the repository root. Requires Python 3, outbound HTTPS to GitHub's API and release downloads, and space for the full compressed baseline plus the inner ZIP (for wrapped artifacts) and the JSON Lines output. No API key or extra Python packages are required.

Output: `cves.jsonl`, one complete CVE JSON record per line, ordered by archive path with sorted object keys. Includes rejected records as provided upstream; consumers can filter `cveMetadata.state`. This is the latest daily baseline, not the hourly delta feed. The full archive is downloaded on every run, validated without extracting paths, and discarded. The output is atomically replaced only when content changes; download or validation failure preserves the previous snapshot and exits unsuccessfully. The initial run needs network access before output is available.

The large generated dataset and temporary downloads are ignored by Git. Check `stderr.log` for execution failures. Run collector checks with `python3 -m unittest discover -s database/20261020201233-ac997a4a21deafd6edfb -p 'test_*.py'`.

Baseline discovery accepts both the documented `YYYY-MM-DD_all_CVEs_at_midnight.zip` and the upstream `YYYY-MM-DD_all_CVEs_at_midnight.zip.zip` naming variant. The latter is a GitHub artifact wrapper containing `cves.zip`; the collector stages that inner ZIP on disk before reading its CVE JSON records. Direct JSON ZIP archives are also supported. Archive paths are never extracted to the filesystem. See the [official 2026-10-19 midnight assets](https://github.com/CVEProject/cvelistV5/releases/expanded_assets/cve_2026-10-19_0000Z). Previously the exact single-extension matcher skipped these assets and raised `ValueError: No official daily CVE baseline found` even when a baseline was available.
