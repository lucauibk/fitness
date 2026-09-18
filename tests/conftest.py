"""Testdatenbank: eine leere SQLite-Datei pro Testlauf, komplett getrennt von fitness.db.
FITNESS_DB muss stehen, bevor app.db zum ersten Mal importiert wird."""
import os
import tempfile

os.environ["FITNESS_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pytest  # noqa: E402

from app import db  # noqa: E402

db.init()


@pytest.fixture
def profile_id():
    """Frisches Profil je Test, damit Tests sich nicht gegenseitig Daten unterschieben."""
    with db.connect() as c:
        cur = c.execute("INSERT INTO profiles (name, created_at) VALUES (?, ?)",
                        (f"Test-{os.urandom(4).hex()}", db.now()))
        return cur.lastrowid
