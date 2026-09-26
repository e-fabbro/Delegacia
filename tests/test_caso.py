"""Critério de aceite F0: `caso novo`, `caso status`, `caso estado`."""
import json
import os
import sqlite3
import stat
import subprocess
import sys

import pytest

from agencia import caso


def cli(*args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agencia", *args], capture_output=True, text=True, env=env
    )


# ---------- caso novo ----------

def test_novo_cria_estrutura(raiz_casos):
    saida = caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    for sub in ("00_brutos", "01_custodia", "02_extraido", "03_analises", "04_produtos", "04_produtos/render", "_cofre", "log"):
        assert (d / sub).is_dir(), sub
    assert (d / "estado.json").is_file()
    assert (d / "caso.db").is_file()
    assert (d / "_cofre" / "identidades.db").is_file()
    assert saida["codinome"] == "TESTE"
    assert saida["caminho"] == str(d)
    assert saida["brutos"] == str(d / "00_brutos")


def test_novo_permissoes(raiz_casos):
    caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE((d / "_cofre").stat().st_mode) == 0o700
    assert stat.S_IMODE((d / "_cofre" / "identidades.db").stat().st_mode) == 0o600
    assert stat.S_IMODE((d / "00_brutos").stat().st_mode) == 0o700


def test_novo_estado_inicial(raiz_casos):
    caso.novo("TESTE")
    estado = json.loads((raiz_casos / "TESTE" / "estado.json").read_text(encoding="utf-8"))
    assert estado["codinome"] == "TESTE"
    assert estado["fase"] == "novo"
    assert estado["agentes_concluidos"] == []
    assert estado["ciclos_revisao"] == {}
    assert estado["pendencias"] == []
    assert estado["alertas"] == []
    assert estado["criado_em"]
    assert estado["atualizado_em"]


def test_novo_bancos_sqlite_validos(raiz_casos):
    caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    for db in (d / "caso.db", d / "_cofre" / "identidades.db"):
        con = sqlite3.connect(db)
        versao = con.execute("select valor from meta where chave='schema_version'").fetchone()
        con.close()
        assert versao is not None


def test_novo_recusa_duplicado(raiz_casos):
    caso.novo("TESTE")
    with pytest.raises(caso.ErroCaso, match="já existe"):
        caso.novo("TESTE")


@pytest.mark.parametrize("ruim", ["teste", "../X", "A B", "", "X", "TESTE/00", "-TESTE", "T" * 40])
def test_novo_recusa_codinome_invalido(raiz_casos, ruim):
    with pytest.raises(caso.ErroCaso, match="codinome"):
        caso.novo(ruim)
    assert not list(raiz_casos.iterdir())


def test_raiz_padrao_srv_casos(monkeypatch):
    monkeypatch.delenv("AGENCIA_CASOS", raising=False)
    assert str(caso.raiz_casos()) == "/srv/casos"


# ---------- caso status ----------

def test_status_caso_novo(raiz_casos):
    caso.novo("TESTE")
    s = caso.status("TESTE")
    assert s["codinome"] == "TESTE"
    assert s["fase"] == "novo"
    assert s["brutos"] == {"arquivos": 0, "ingeridos": 0, "pendentes": 0}
    assert s["documentos"] == 0
    assert s["agentes_concluidos"] == []
    assert s["pendencias"] == []
    assert s["alertas"] == []
    assert s["produtos"] == []
    assert "manifesto" not in s


def test_status_conta_brutos_pendentes(raiz_casos):
    caso.novo("TESTE")
    brutos = raiz_casos / "TESTE" / "00_brutos"
    (brutos / "rif.pdf").write_bytes(b"x")
    (brutos / "simba.csv").write_bytes(b"y")
    s = caso.status("TESTE")
    assert s["brutos"] == {"arquivos": 2, "ingeridos": 0, "pendentes": 2}


def test_status_usa_manifesto(raiz_casos):
    caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    (d / "00_brutos" / "rif.pdf").write_bytes(b"x")
    (d / "00_brutos" / "novo.pdf").write_bytes(b"y")
    manifesto = [{"doc_id": "DOC-001", "nome": "rif.pdf", "sha256": "ab" * 32, "tamanho": 1, "tipo": "RIF"}]
    (d / "01_custodia" / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    s = caso.status("TESTE", manifesto=True)
    assert s["documentos"] == 1
    assert s["brutos"] == {"arquivos": 2, "ingeridos": 1, "pendentes": 1}
    assert s["manifesto"] == manifesto


def test_status_lista_produtos(raiz_casos):
    caso.novo("TESTE")
    (raiz_casos / "TESTE" / "04_produtos" / "informacao_analise_v1.md").write_text("x", encoding="utf-8")
    assert caso.status("TESTE")["produtos"] == ["informacao_analise_v1.md"]


def test_status_caso_inexistente(raiz_casos):
    with pytest.raises(caso.ErroCaso, match="não existe"):
        caso.status("NADA")


# ---------- caso estado ----------

def test_estado_set_fase(raiz_casos):
    caso.novo("TESTE")
    e = caso.estado("TESTE", sets=["fase=ingestao"])
    assert e["fase"] == "ingestao"
    assert json.loads((raiz_casos / "TESTE" / "estado.json").read_text(encoding="utf-8"))["fase"] == "ingestao"


def test_estado_set_valor_json_e_aninhado(raiz_casos):
    caso.novo("TESTE")
    e = caso.estado("TESTE", sets=["ciclos_revisao.analista-rif=2", 'pendencias=["a","b"]'])
    assert e["ciclos_revisao"] == {"analista-rif": 2}
    assert e["pendencias"] == ["a", "b"]


def test_estado_add_lista_idempotente(raiz_casos):
    caso.novo("TESTE")
    caso.estado("TESTE", adds=["agentes_concluidos=analista-rif"])
    e = caso.estado("TESTE", adds=["agentes_concluidos=analista-rif", "alertas=texto suspeito em DOC-002"])
    assert e["agentes_concluidos"] == ["analista-rif"]
    assert e["alertas"] == ["texto suspeito em DOC-002"]


def test_estado_atualiza_timestamp(raiz_casos):
    caso.novo("TESTE")
    antes = caso.estado("TESTE")["atualizado_em"]
    depois = caso.estado("TESTE", sets=["fase=analise"])["atualizado_em"]
    assert depois >= antes


def test_estado_sem_alteracao_devolve_atual(raiz_casos):
    caso.novo("TESTE")
    assert caso.estado("TESTE")["fase"] == "novo"


def test_estado_recusa_fase_desconhecida(raiz_casos):
    caso.novo("TESTE")
    with pytest.raises(caso.ErroCaso, match="fase"):
        caso.estado("TESTE", sets=["fase=qualquer"])


def test_estado_recusa_codinome_e_formato(raiz_casos):
    caso.novo("TESTE")
    with pytest.raises(caso.ErroCaso):
        caso.estado("TESTE", sets=["codinome=OUTRO"])
    with pytest.raises(caso.ErroCaso, match="chave=valor"):
        caso.estado("TESTE", sets=["semigual"])
    with pytest.raises(caso.ErroCaso, match="lista"):
        caso.estado("TESTE", adds=["fase=x"])


# ---------- CLI ----------

def test_cli_caso_novo_json(raiz_casos):
    p = cli("caso", "novo", "TESTE")
    assert p.returncode == 0, p.stderr
    saida = json.loads(p.stdout)
    assert saida["brutos"] == str(raiz_casos / "TESTE" / "00_brutos")


def test_cli_caso_status_json_e_md(raiz_casos):
    cli("caso", "novo", "TESTE")
    p = cli("caso", "status", "TESTE")
    assert p.returncode == 0
    assert json.loads(p.stdout)["fase"] == "novo"
    p = cli("caso", "status", "TESTE", "--md")
    assert p.returncode == 0
    assert p.stdout.startswith("| ")
    assert "| fase | novo |" in p.stdout


def test_cli_caso_status_manifesto_md(raiz_casos):
    cli("caso", "novo", "TESTE")
    p = cli("caso", "status", "TESTE", "--manifesto", "--md")
    assert p.returncode == 0
    assert "manifesto" in p.stdout


def test_cli_caso_estado_set(raiz_casos):
    cli("caso", "novo", "TESTE")
    p = cli("caso", "estado", "TESTE", "--set", "fase=analise", "--add", "agentes_concluidos=analista-rif")
    assert p.returncode == 0, p.stderr
    e = json.loads(p.stdout)
    assert e["fase"] == "analise"
    assert e["agentes_concluidos"] == ["analista-rif"]


def test_cli_erro_em_json_no_stderr(raiz_casos):
    p = cli("caso", "status", "NADA")
    assert p.returncode == 1
    assert p.stdout == ""
    assert "não existe" in json.loads(p.stderr)["erro"]


def test_cli_sem_argumentos_mostra_uso():
    p = cli()
    assert p.returncode == 2
    assert "caso" in p.stderr
