from __future__ import annotations

import pytest
import requests


@pytest.fixture(autouse=True)
def _patch_musicbrainz_session(monkeypatch: pytest.MonkeyPatch):
    """Use plain session wherever MB requests are mocked.

    This avoids rate limiting requests to speed up tests.
    Applied automatically (autouse=True) to all plugin tests,
    so no need to rename/re-export requests_mock fixture.
    """
    monkeypatch.setattr(
        "beetsplug._utils.musicbrainz.MusicBrainzAPI.create_session",
        lambda _: requests.Session(),
    )
