from __future__ import annotations

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
