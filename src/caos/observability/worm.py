"""WORM Storage — Write-Once-Read-Many immutable verdict logs.

Implements tamper-evident, append-only storage for CAOS verdict records.
Each record is chained via SHA-256 hash to the previous record, enabling
integrity verification of the entire audit trail (doc §9).

Usage:
    storage = get_worm_storage()
    storage.append({"action_id": "act_abc", "verdict_score": 0.6, ...})
    ok, error_pos = storage.verify()
"""

import hashlib
import json
import os
import structlog
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from caos.config import get_settings

logger = structlog.get_logger(__name__)

# Genesis hash (first record chains from this)
GENESIS_HASH = "0" * 64


class WORMStorage(ABC):
    """Abstract base for write-once-read-many storage."""

    @abstractmethod
    def append(self, record: dict[str, Any]) -> str:
        """Append a record. Returns the record's hash."""
        ...

    @abstractmethod
    def verify(self) -> tuple[bool, int | None]:
        """Verify hash chain integrity.

        Returns:
            (True, None) if chain is valid.
            (False, line_number) if tampered — line_number is 0-indexed.
        """
        ...

    @abstractmethod
    def read_all(
        self,
        tenant_id: str | None = None,
        asset_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read records with optional filters. Returns (records, total)."""
        ...

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        """Return storage statistics."""
        ...


class FileWORMStorage(WORMStorage):
    """Append-only JSONL files with SHA-256 hash chain.

    Storage layout:
        {base_dir}/verdicts_YYYY-MM-DD.jsonl

    Each line is a JSON object with a `_hash` field that chains to the
    previous record's hash, forming a tamper-evident linked list.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        settings = get_settings()
        self._base_dir = Path(base_dir or settings.worm_storage_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_hash: str | None = None  # cached for fast append

        # Initialize last hash from most recent file
        self._last_hash = self._load_last_hash()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _today_file(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._base_dir / f"verdicts_{today}.jsonl"

    def _all_files(self) -> list[Path]:
        """Return all verdict files sorted chronologically."""
        files = sorted(self._base_dir.glob("verdicts_*.jsonl"))
        return files

    def _load_last_hash(self) -> str:
        """Load the hash of the last record across all files."""
        files = self._all_files()
        if not files:
            return GENESIS_HASH
        # Read last line of the most recent file
        last_file = files[-1]
        try:
            with open(last_file, "r") as f:
                last_line = ""
                for line in f:
                    line = line.strip()
                    if line:
                        last_line = line
                if last_line:
                    record = json.loads(last_line)
                    return record.get("_hash", GENESIS_HASH)
        except Exception:
            pass
        return GENESIS_HASH

    @staticmethod
    def _compute_hash(prev_hash: str, data: dict[str, Any]) -> str:
        """Compute SHA-256 hash chaining previous hash with record data."""
        # Remove internal metadata from data to avoid circular/inconsistent hashing
        internal_keys = {"_hash", "_prev_hash", "_timestamp"}
        clean = {k: v for k, v in data.items() if k not in internal_keys}
        payload = prev_hash + json.dumps(clean, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, record: dict[str, Any]) -> str:
        """Append a verdict record to the WORM store.

        Adds timestamp, sequence hash, and writes to today's file.
        Returns the record's hash.
        """
        with self._lock:
            # Add metadata
            record["_timestamp"] = datetime.now(timezone.utc).isoformat()

            # Compute chain hash
            prev = self._last_hash or GENESIS_HASH
            record_hash = self._compute_hash(prev, record)
            record["_prev_hash"] = prev
            record["_hash"] = record_hash

            # Append to file (O_APPEND for atomicity on POSIX)
            filepath = self._today_file()
            with open(filepath, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")

            self._last_hash = record_hash

            logger.info(
                "worm_record_appended",
                file=filepath.name,
                hash=record_hash[:16],
                action_id=record.get("action_id"),
            )
            return record_hash

    def verify(self) -> tuple[bool, int | None]:
        """Verify the integrity of the entire hash chain.

        Reads all files chronologically and validates each record's hash
        against the previous record's hash.

        Returns:
            (True, None) if chain is intact.
            (False, line_index) if chain is broken at the given line.
        """
        prev_hash = GENESIS_HASH
        line_index = 0

        for filepath in self._all_files():
            try:
                with open(filepath, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            logger.error("worm_verify_json_error", line=line_index)
                            return False, line_index

                        stored_hash = record.get("_hash")
                        stored_prev = record.get("_prev_hash", GENESIS_HASH)

                        # Verify prev-hash continuity
                        if stored_prev != prev_hash:
                            logger.error(
                                "worm_verify_chain_break",
                                line=line_index,
                                expected_prev=prev_hash[:16],
                                stored_prev=stored_prev[:16],
                            )
                            return False, line_index

                        # Verify record hash
                        expected_hash = self._compute_hash(prev_hash, record)
                        if stored_hash != expected_hash:
                            logger.error(
                                "worm_verify_hash_mismatch",
                                line=line_index,
                                expected=expected_hash[:16],
                                stored=stored_hash[:16] if stored_hash else "None",
                            )
                            return False, line_index

                        prev_hash = stored_hash
                        line_index += 1
            except Exception as e:
                logger.error("worm_verify_file_error", file=filepath.name, error=str(e))
                return False, line_index

        logger.info("worm_verify_ok", total_records=line_index)
        return True, None

    def read_all(
        self,
        tenant_id: str | None = None,
        asset_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read records with optional filters."""
        records: list[dict[str, Any]] = []

        # Determine which files to read based on date filters
        files = self._all_files()
        if date_from:
            files = [f for f in files if f.stem.split("_", 1)[-1] >= date_from]
        if date_to:
            files = [f for f in files if f.stem.split("_", 1)[-1] <= date_to]

        for filepath in files:
            try:
                with open(filepath, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Apply filters
                        if tenant_id and record.get("tenant_id") != tenant_id:
                            continue
                        if asset_id and record.get("asset_id") != asset_id:
                            continue

                        records.append(record)
            except Exception:
                continue

        total = len(records)
        # Reverse for newest-first, then paginate
        records.reverse()
        paginated = records[offset : offset + limit]
        return paginated, total

    def stats(self) -> dict[str, Any]:
        """Return storage statistics."""
        files = self._all_files()
        total_records = 0
        total_bytes = 0
        oldest_date = None
        newest_date = None

        for filepath in files:
            size = filepath.stat().st_size
            total_bytes += size
            date_str = filepath.stem.split("_", 1)[-1]
            if oldest_date is None or date_str < oldest_date:
                oldest_date = date_str
            if newest_date is None or date_str > newest_date:
                newest_date = date_str
            with open(filepath, "r") as f:
                total_records += sum(1 for line in f if line.strip())

        return {
            "total_records": total_records,
            "total_files": len(files),
            "total_bytes": total_bytes,
            "oldest_date": oldest_date,
            "newest_date": newest_date,
            "storage_dir": str(self._base_dir),
        }


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_worm_storage: FileWORMStorage | None = None


def get_worm_storage() -> FileWORMStorage:
    """Get or create the global WORM storage singleton."""
    global _worm_storage
    if _worm_storage is None:
        _worm_storage = FileWORMStorage()
    return _worm_storage
