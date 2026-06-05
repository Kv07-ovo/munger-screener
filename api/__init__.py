"""Read-only HTTP API layer for the Kv stock-cat web migration.

This package is a thin transport shell over the existing Python business logic
(`research_service.run_research`). It adds NO scoring/business behavior of its own and
never writes data (always readonly=True). See api/adapters.py for the testable core.
"""
