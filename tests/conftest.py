import pytest

RAIZ_PROJETO = __import__("pathlib").Path(__file__).resolve().parent.parent


@pytest.fixture
def raiz_casos(tmp_path, monkeypatch):
    """Raiz de casos temporária: nenhum teste toca /srv/casos."""
    raiz = tmp_path / "casos"
    raiz.mkdir()
    monkeypatch.setenv("AGENCIA_CASOS", str(raiz))
    return raiz


@pytest.fixture
def raiz_projeto():
    return RAIZ_PROJETO
