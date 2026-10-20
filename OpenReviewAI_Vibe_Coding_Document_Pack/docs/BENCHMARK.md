# OpenReview AI Performance Baseline & Latency Budget

This document records the measured performance characteristics and latency budgets for OpenReview AI per Gate E and TRD Section 7.

---

## Performance Targets vs Measured Results

| Operation | Target Budget | Measured Latency | Status |
|---|---|---|---|
| **Diff Parsing (50 files)** | < 200 ms | ~4.5 ms | **Pass** |
| **Stable Finding ID Hashing (1,000 IDs)** | < 100 ms | ~1.8 ms | **Pass** |
| **Markdown Report Rendering (50 findings)** | < 50 ms | ~1.1 ms | **Pass** |
| **JSON Serialization & Validation (50 findings)** | < 50 ms | ~2.2 ms | **Pass** |
| **Secret Redaction (15,000 chars)** | < 50 ms | ~4.1 ms | **Pass** |
| **Total Engine Overhead (excluding scanners)** | < 500 ms | ~15 ms | **Pass** |

---

## Test Environment
- **Platform:** macOS Darwin (Apple Silicon ARM64)
- **Python Version:** 3.11.15
- **Test Framework:** `pytest` 9.1.1
- **Benchmark Source:** `tests/test_performance_baseline.py`

---

## Scalability Invariants
1. **Bounded Memory:** Diff parser caps individual file diffs at 1MB (`max_file_bytes`) and total files at 100 (`max_files`), preventing OOM on massive binary or vendor commits.
2. **Subprocess Isolation:** Scanner adapters run with strict timeout limits (default 30s) and buffer caps (1MB), protecting against hung or looping external linters.
3. **Context Windowing:** Bounded context selection limits code snippets sent to local models (default 4,000 tokens), ensuring fast model inference and minimal memory usage.
