# Legacy Runner Script Archive (`run.sh`)

**Archived Date:** August 2, 2026  
**Context:** Relocated upon the official release of `run_with_check.sh` (v1.2.0).

---

## Overview

This directory stores the lightweight execution wrapper script (`run.sh`) from earlier releases.

* **Key Characteristic:** Pure non-invasive execution wrapper focusing strictly on maximum reduction speed and zero runtime overhead.
* **Operational Note:** Unlike the v1.2.0 fail-safe runner (`run_with_check.sh`), this legacy script **does not** include automated pre-execution backup, structural integrity verification, or automatic rollback mechanisms.

It is retained here for performance benchmark comparisons and historical architectural reference. For production pipelines requiring absolute reliability, please use `run_with_check.sh` located in the main package directory.
