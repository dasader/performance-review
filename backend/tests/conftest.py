import os

# ponytail: app.config imports a module-level `settings = Settings()` singleton,
# which requires these env vars even when a test only needs Settings(...) with
# explicit kwargs. Set harmless defaults here (not a .env file) so importing
# app.config doesn't crash under pytest. Explicit kwargs in tests still win.
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("OPENALEX_API_KEY", "test-key")
os.environ.setdefault("ADMIN_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test/test")
