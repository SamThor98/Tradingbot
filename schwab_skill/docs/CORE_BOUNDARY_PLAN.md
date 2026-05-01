# Shared Core Boundary Plan

This document defines the domain/application/infrastructure boundaries for
incremental extraction from entrypoint-centric code.

## Target Layers

1. **Core domain (`core/`)**
   - Pure orchestration helpers and contract shaping.
   - No FastAPI/Celery/DB dependencies.
2. **Application services (`webapp/`)**
   - Request handling, persistence, tenancy scoping, auth.
3. **Infrastructure adapters**
   - Schwab auth/client, DB sessions, Celery runtime, Redis queues.

## First Extracted Slice (Implemented)

- `core/scan_service.py`
  - `run_scan(...)` wraps `scan_for_signals_detailed(...)` as a shared domain call.
  - `summarize_live_strategy(...)` centralizes strategy attribution summaries.
- `core/execution_service.py`
  - `submit_order(...)` wraps order submission contract.

## Adopted Call Paths

- Local dashboard (`webapp/main.py`) now calls `core.scan_service.run_scan`.
- SaaS worker path (`webapp/tasks.py`) now calls `core.scan_service.run_scan`
  and `core.execution_service.submit_order`.
- SaaS tenant routes (`webapp/tenant_dashboard.py`) now call shared core scan
  and execution service functions for onboarding and approval flows.

## Next Slices

1. Extract diagnostics shaping into `core/diagnostics_service.py`.
2. Extract promotion decision checks into a core governance service.
3. Move hypothesis scoring orchestration into a core registry service.
