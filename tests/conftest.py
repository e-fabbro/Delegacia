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


@pytest.fixture(autouse=True)
def _auditoria_isolada(tmp_path, monkeypatch):
    """Nenhum teste grava na auditoria real (/var/log/agencia-nexo)."""
    monkeypatch.setenv("AGENCIA_AUDIT_LOG", str(tmp_path / "_auditoria_teste.jsonl"))
