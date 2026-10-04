from __future__ import annotations

import os
import tempfile

# Settings and the SQLAlchemy engine are created at import time, so the test
# database has to be configured before anything under `app` is imported.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://examable:examable@localhost:5432/examable_test"
)
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="examable-test-uploads-")
os.environ["ADMIN_TOKEN"] = ""
os.environ["MULTIMODAL_ENABLED"] = "false"
os.environ["MULTIMODAL_API_KEY"] = ""
