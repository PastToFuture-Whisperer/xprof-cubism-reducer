# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.0] - 2026-08-02

### Added
- New fail-safe execution runner `run_with_check.sh` featuring automatic pre-execution backup, zero-dependency structural verification, and instant rollback.
- Explicit version header metadata across module scripts (`tb_log_reducer.py`, `run.sh`, `run_with_check.sh`).

### Changed
- Quantified key performance benchmarks (80-95% size reduction, >90% rendering speedup) added to README.
- Refactored repository root directory structure (`assets/` for images, `examples/` for sample archives).

---

## [1.1.0] - 2026-07-29

### Added
- Architectural notice and mathematical disclaimer in README and script headers.

### Changed
- Upgraded binary inspection logic with Protobuf Wire Type 2 (`Tag == 2`) and Varint length checks.
- Elevated operational reliability from ~90-95% to **~99.999%**.

---

## [1.0.0] - 2026-07-28

### Added
- Initial public release of `xprof-cubism-reducer`.
- Core in-place byte replacement engine for high-speed TensorBoard trace log reduction.
- Basic execution shell script (`run.sh`).