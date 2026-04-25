"""Shared pytest fixtures for the helpdesk test suite."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_fetch_one():
    with patch("models.db.fetch_one") as m:
        yield m


@pytest.fixture
def mock_db_execute():
    with patch("models.db.execute") as m:
        yield m
