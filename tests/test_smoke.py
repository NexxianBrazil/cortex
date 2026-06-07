"""Smoke test: garante que o pacote cortex importa sem erro."""

import cortex


def test_cortex_importa():
    assert cortex.__version__
