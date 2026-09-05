"""Tests for the latest observation index in the edge collector."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from edge import skcounter_edge


class TestLatestObservationIndex:
    """Test the latest observation index functionality."""

    def test_derive_index_keys(self):
        """Test that index keys are derived correctly from observations."""
        observation = {
            "schema_version": "skcounter.snapshot.v1",
            "measurement_lane": "harness_reported",
            "node_id": "chiap01",
            "principal_id": "user1",
            "observed_at": "2026-08-31T12:00:00Z",
            "payload_hash": "a" * 64,
            "idempotency_key": "b" * 64,
            "aggregates": [
                {
                    "view": "daily",
                    "bucket_start": "2026-08-31T00:00:00Z",
                    "tokens": {"input": 100, "output": 50, "total": 150},
                },
                {
                    "view": "hourly",
                    "bucket_start": "2026-08-31T12:00:00Z",
                    "tokens": {"input": 50, "output": 25, "total": 75},
                },
            ],
        }

        keys = skcounter_edge._derive_index_keys(observation)

        assert len(keys) == 2
        assert "harness_reported:chiap01:user1:daily:2026-08-31T00:00:00Z" in keys
        assert "harness_reported:chiap01:user1:hourly:2026-08-31T12:00:00Z" in keys

    def test_write_index_atomically(self):
        """Test that index is written atomically using temp file then rename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "latest-observation-index.jsonl"
            index_data = {
                "key1": {
                    "schema_version": "skcounter.latest-observation-index.v1",
                    "key": "key1",
                    "observation_path": "test.json",
                    "observed_at": "2026-08-31T10:00:00Z",
                    "payload_hash": "a" * 64,
                    "idempotency_key": "b" * 64,
                }
            }

            skcounter_edge._write_index_atomically(index_path, index_data)

            # Verify index file exists and is valid
            assert index_path.exists()
            content = index_path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")

            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["key"] == "key1"

            # Verify temp file was cleaned up
            tmp_files = list(Path(tmpdir).glob("*.tmp"))
            assert len(tmp_files) == 0

    def test_update_latest_index_creates_new_entry(self):
        """Test that updating index creates a new entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            config = {
                "state_dir": str(state_dir),
            }

            observation = {
                "schema_version": "skcounter.snapshot.v1",
                "measurement_lane": "harness_reported",
                "node_id": "chiap01",
                "principal_id": "user1",
                "observed_at": "2026-08-31T10:00:00Z",
                "payload_hash": "a" * 64,
                "idempotency_key": "b" * 64,
                "aggregates": [
                    {
                        "view": "daily",
                        "bucket_start": "2026-08-31T00:00:00Z",
                        "tokens": {"input": 100, "output": 50, "total": 150},
                    }
                ],
            }

            observation_path = state_dir / "sent" / "chiap01" / "user1" / "test.json"
            observation_path.parent.mkdir(parents=True, exist_ok=True)
            observation_path.touch()

            skcounter_edge._update_latest_index(config, observation_path, observation)

            index_path = state_dir / "latest-observation-index.jsonl"
            assert index_path.exists()

            content = index_path.read_text(encoding="utf-8")
            entry = json.loads(content.strip())

            assert entry["key"] == "harness_reported:chiap01:user1:daily:2026-08-31T00:00:00Z"
            assert entry["observed_at"] == "2026-08-31T10:00:00Z"
            assert entry["payload_hash"] == "a" * 64

    def test_update_latest_index_replaces_older_entry(self):
        """Test that newer observations replace older ones for the same key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            config = {
                "state_dir": str(state_dir),
            }

            old_observation = {
                "schema_version": "skcounter.snapshot.v1",
                "measurement_lane": "harness_reported",
                "node_id": "chiap01",
                "principal_id": "user1",
                "observed_at": "2026-08-31T10:00:00Z",
                "payload_hash": "a" * 64,
                "idempotency_key": "b" * 64,
                "aggregates": [
                    {
                        "view": "daily",
                        "bucket_start": "2026-08-31T00:00:00Z",
                        "tokens": {"input": 100, "output": 50, "total": 150},
                    }
                ],
            }

            new_observation = {
                "schema_version": "skcounter.snapshot.v1",
                "measurement_lane": "harness_reported",
                "node_id": "chiap01",
                "principal_id": "user1",
                "observed_at": "2026-08-31T11:00:00Z",
                "payload_hash": "c" * 64,
                "idempotency_key": "d" * 64,
                "aggregates": [
                    {
                        "view": "daily",
                        "bucket_start": "2026-08-31T00:00:00Z",
                        "tokens": {"input": 200, "output": 100, "total": 300},
                    }
                ],
            }

            observation_path = state_dir / "sent" / "chiap01" / "user1" / "test.json"
            observation_path.parent.mkdir(parents=True, exist_ok=True)
            observation_path.touch()

            # Add old observation
            skcounter_edge._update_latest_index(config, observation_path, old_observation)

            # Add new observation for same key
            skcounter_edge._update_latest_index(config, observation_path, new_observation)

            index_path = state_dir / "latest-observation-index.jsonl"
            content = index_path.read_text(encoding="utf-8")
            entry = json.loads(content.strip())

            # Should have newer timestamp
            assert entry["observed_at"] == "2026-08-31T11:00:00Z"
            assert entry["payload_hash"] == "c" * 64

    def test_update_index_enforces_10000_entry_limit(self):
        """Test that index enforces 10000 entry limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            config = {
                "state_dir": str(state_dir),
            }

            observation_path = state_dir / "sent" / "chiap01" / "user1" / "test.json"
            observation_path.parent.mkdir(parents=True, exist_ok=True)
            observation_path.touch()

            # Add 10050 entries
            for i in range(10050):
                observation = {
                    "schema_version": "skcounter.snapshot.v1",
                    "measurement_lane": "harness_reported",
                    "node_id": "chiap01",
                    "principal_id": "user1",
                    "observed_at": f"2026-08-31T{str(i).zfill(2)}:00:00Z",
                    "payload_hash": "a" * 64,
                    "idempotency_key": str(i).zfill(64),
                    "aggregates": [
                        {
                            "view": "daily",
                            "bucket_start": f"2026-08-31T{str(i).zfill(2)}:00:00Z",
                            "tokens": {"input": i, "output": i, "total": i * 2},
                        }
                    ],
                }
                skcounter_edge._update_latest_index(config, observation_path, observation)

            index_path = state_dir / "latest-observation-index.jsonl"
            content = index_path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")

            # Should have at most 10000 entries
            assert len(lines) <= 10000

    def test_update_index_preserves_valid_index_on_malformed_input(self):
        """Test that malformed index file doesn't break updates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            config = {
                "state_dir": str(state_dir),
            }

            index_path = state_dir / "latest-observation-index.jsonl"
            # Write malformed index
            index_path.write_text("{not valid json", encoding="utf-8")

            observation = {
                "schema_version": "skcounter.snapshot.v1",
                "measurement_lane": "harness_reported",
                "node_id": "chiap01",
                "principal_id": "user1",
                "observed_at": "2026-08-31T10:00:00Z",
                "payload_hash": "a" * 64,
                "idempotency_key": "b" * 64,
                "aggregates": [
                    {
                        "view": "daily",
                        "bucket_start": "2026-08-31T00:00:00Z",
                        "tokens": {"input": 100, "output": 50, "total": 150},
                    }
                ],
            }

            observation_path = state_dir / "sent" / "chiap01" / "user1" / "test.json"
            observation_path.parent.mkdir(parents=True, exist_ok=True)
            observation_path.touch()

            # Should not raise, should overwrite with valid index
            skcounter_edge._update_latest_index(config, observation_path, observation)

            # Index should now be valid
            content = index_path.read_text(encoding="utf-8")
            entry = json.loads(content.strip())
            assert entry["schema_version"] == "skcounter.latest-observation-index.v1"

    def test_update_index_handles_missing_aggregates(self):
        """Test that observations without aggregates are handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            config = {
                "state_dir": str(state_dir),
            }

            observation = {
                "schema_version": "skcounter.snapshot.v1",
                "measurement_lane": "harness_reported",
                "node_id": "chiap01",
                "principal_id": "user1",
                "observed_at": "2026-08-31T10:00:00Z",
                "payload_hash": "a" * 64,
                "idempotency_key": "b" * 64,
                "aggregates": [],
            }

            observation_path = state_dir / "sent" / "chiap01" / "user1" / "test.json"
            observation_path.parent.mkdir(parents=True, exist_ok=True)
            observation_path.touch()

            # Should not raise
            skcounter_edge._update_latest_index(config, observation_path, observation)

            # Index should exist even with empty aggregates
            index_path = state_dir / "latest-observation-index.jsonl"
            # No keys generated from empty aggregates
            content = index_path.read_text(encoding="utf-8").strip()
            assert content == ""

    def test_write_index_is_append_only_per_line(self):
        """Test that each line is a complete JSON entry (append-only structure)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            config = {
                "state_dir": str(state_dir),
            }

            observation_path = state_dir / "sent" / "chiap01" / "user1" / "test.json"
            observation_path.parent.mkdir(parents=True, exist_ok=True)
            observation_path.touch()

            # Add multiple entries
            for i in range(5):
                observation = {
                    "schema_version": "skcounter.snapshot.v1",
                    "measurement_lane": "harness_reported",
                    "node_id": "chiap01",
                    "principal_id": "user1",
                    "observed_at": f"2026-08-31T1{i}:00:00Z",
                    "payload_hash": "a" * 64,
                    "idempotency_key": str(i).zfill(64),
                    "aggregates": [
                        {
                            "view": "daily",
                            "bucket_start": f"2026-08-31T{i}:00:00Z",
                            "tokens": {"input": i, "output": i, "total": i * 2},
                        }
                    ],
                }
                skcounter_edge._update_latest_index(config, observation_path, observation)

            index_path = state_dir / "latest-observation-index.jsonl"
            content = index_path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")

            # Each line should be valid JSON
            for line in lines:
                entry = json.loads(line)
                assert "schema_version" in entry
                assert "key" in entry
                assert "observed_at" in entry
