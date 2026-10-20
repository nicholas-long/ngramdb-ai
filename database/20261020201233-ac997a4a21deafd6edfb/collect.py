#!/usr/bin/env python3
"""Materialize the official daily CVE baseline as deterministic JSON Lines."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request
import zipfile

API = "https://api.github.com/repos/CVEProject/cvelistV5/releases"
# Upstream also publishes baselines with a doubled .zip extension.
BASELINE = re.compile(r"\d{4}-\d{2}-\d{2}_all_CVEs_at_midnight\.zip(?:\.zip)?")
CVE = re.compile(r"CVE-\d{4}-\d{4,}\.json")


def request(url):
    return urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "NgramDB-CVE-collector", "Accept": "application/json"}
    ), timeout=120)


def latest_baseline():
    # Hourly delta releases can appear ahead of the most recent full baseline.
    for page in range(1, 4):
        with request(f"{API}?per_page=100&page={page}") as response:
            releases = json.load(response)
        candidates = [asset for release in releases
                      if not release.get("draft") and not release.get("prerelease")
                      for asset in release.get("assets", [])
                      if BASELINE.fullmatch(asset["name"])]
        if candidates:
            return max(candidates, key=lambda asset: asset["name"])
        if len(releases) < 100:
            break
    raise ValueError("No official daily CVE baseline found")


@contextmanager
def record_archive(archive, staging_directory):
    """Read direct records or the cves.zip inside GitHub's artifact wrapper."""
    with zipfile.ZipFile(archive) as outer:
        if any(CVE.fullmatch(info.filename.rsplit('/', 1)[-1]) for info in outer.infolist()):
            yield outer
            return
        nested = [info for info in outer.infolist()
                  if not info.is_dir() and info.filename.rsplit('/', 1)[-1] == 'cves.zip']
        if not nested:
            yield outer  # write_records reports an empty/unsupported baseline.
            return
        if len(nested) != 1:
            raise ValueError('Baseline contains multiple cves.zip archives')
        # Zip readers need random access. Unpack only the inner archive to a
        # temporary file, never to memory or paths supplied by the archive.
        with tempfile.TemporaryFile(dir=staging_directory) as staged:
            with outer.open(nested[0]) as source:
                shutil.copyfileobj(source, staged)
            staged.seek(0)
            with zipfile.ZipFile(staged) as inner:
                yield inner


def write_records(archive, destination):
    count = 0
    seen = set()
    with record_archive(archive, Path(destination).parent) as source, open(destination, "w", encoding="utf-8") as output:
        for name in sorted(source.namelist()):
            filename = name.rsplit("/", 1)[-1]
            if not CVE.fullmatch(filename):
                continue
            with source.open(name) as record_file:
                record = json.load(record_file)
            identifier = filename[:-5]
            if (record.get("dataType") != "CVE_RECORD"
                    or record.get("cveMetadata", {}).get("cveId") != identifier
                    or identifier in seen):
                raise ValueError(f"Invalid or duplicate CVE record: {name}")
            seen.add(identifier)
            output.write(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    if not count:
        raise ValueError("Baseline contains no CVE records")
    return count


def publish(staged, destination):
    # Avoid touching unchanged output: downstream graph nodes depend on stability.
    if destination.exists():
        with open(staged, "rb") as new, open(destination, "rb") as old:
            while True:
                chunk = new.read(1024 * 1024)
                if chunk != old.read(1024 * 1024):
                    break
                if not chunk:
                    return
    os.replace(staged, destination)


def main():
    root = Path(__file__).resolve().parent
    asset = latest_baseline()
    # Stage on the destination filesystem, and publish only after full validation.
    with tempfile.TemporaryDirectory(prefix=".cve-", dir=root) as directory:
        stage = Path(directory)
        archive = stage / "baseline.zip"
        with request(asset["browser_download_url"]) as response, open(archive, "wb") as output:
            shutil.copyfileobj(response, output)
        records = stage / "cves.jsonl"
        write_records(archive, records)
        publish(records, root / "cves.jsonl")


if __name__ == "__main__":
    main()
