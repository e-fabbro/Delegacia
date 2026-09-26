"""F2: `rif parse/resumo/comunicacoes/envolvidos/sobreposicao` sobre o RIF sintético de 12 comunicações."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agencia import caso, ingestao, rif
from tests.fixtures.gerar import RIF12, PF1, PF2

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def cent(valor_br: str) -> int:
    return int(valor_br.replace(".", "").replace(",", ""))


def iso(data_br: str) -> str:
    return "-".join(reversed(data_br.split("/")))


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True)


@pytest.fixture
def caso_rif(raiz_casos):
    caso.novo("TESTE")
    shutil.copy(FIXTURES / "rif_12_sintetico.pdf", raiz_casos / "TESTE" / "00_brutos" / "rif_12_sintetico.pdf")
    r = ingestao.ingerir("TESTE")
    assert r["novos"][0]["tipo"] == "RIF"
    return raiz_casos / "TESTE"


def test_parse_extrai_12_comunicacoes_e_cabecalho(caso_rif):
    r = rif.parse("TESTE", "DOC-001")
    assert r["comunicacoes"] == 12
    assert r["por_tipo"] == {"COS": 11, "COA": 1}
    assert r["avisos"] == []
    cab = r["cabecalho"]
    assert cab["numero"] == "67890.2026"
    assert cab["data"] == "2026-07-05"
    assert cab["destinatario"] == "DRCC/PCDF"
    assert cab["tipo_origem"] == "intercambio_a_pedido"
    assert cab["pedido"] == "88/2026-DRCC"
    assert (cab["periodo_inicio"], cab["periodo_fim"]) == ("2026-01-01", "2026-06-30")
    assert cab["total_informado"] == 12
    # soma bruta = soma de todas as comunicações (só referência)
    assert round(r["soma_bruta_nao_consolidada"] * 100) == sum(cent(c[7]) for c in RIF12)


def test_comunicacoes_batem_com_a_fonte(caso_rif):
    rif.parse("TESTE", "DOC-001")
    lista = rif.comunicacoes("TESTE")["comunicacoes"]
    assert len(lista) == 12
    por_num = {c["num"]: c for c in lista}
    for num, tipo, com, seg, dcom, ini, fim, valor, tit, outros, enq, info in RIF12:
        c = por_num[num]
        assert c["tipo"] == tipo
        assert c["segmento"] == seg
        assert c["data_comunicacao"] == iso(dcom)
        assert (c["periodo_inicio"], c["periodo_fim"]) == (iso(ini), iso(fim))
        assert round(c["valor"] * 100) == cent(valor)
        assert c["titular"].startswith("PF-" if "cpf" in tit else "PJ-")
        assert c["enquadramento"] == enq and c["informacoes"] == info
        assert c["envolvidos"].count(":") == 1 + len(outros)
        assert c["ponteiro"] == f"[F:DOC-001:com#{num}]"
    assert por_num[1]["pagina"] == 1 and por_num[4]["pagina"] == 2 and por_num[12]["pagina"] == 4
    # nada em claro
    texto = json.dumps(lista, ensure_ascii=False)
    assert PF1["nome"] not in texto and PF1["cpf"] not in texto and PF2["cpf"] not in texto
    # filtros
    assert rif.comunicacoes("TESTE", tipo="COA")["total"] == 1
    assert rif.comunicacoes("TESTE", envolvido=por_num[1]["titular"])["total"] == 4
    assert rif.comunicacoes("TESTE", limite=5)["exibidas"] == 5


def test_criterio_aceite_1_sobreposicao_e_consolidado(caso_rif):
    rif.parse("TESTE", "DOC-001")
    sob = rif.sobreposicao("TESTE")
    # exatamente as comunicações 1 e 2 (mesmo titular, períodos 05/01–20/02 e 01/02–15/03) se sobrepõem
    assert sob["pares_sobrepostos"] == 1
    par = sob["pares"][0]
    assert {par["a"], par["b"]} == {"[F:DOC-001:com#1]", "[F:DOC-001:com#2]"}
    assert (par["sobreposicao_inicio"], par["sobreposicao_fim"]) == ("2026-02-01", "2026-02-20")
    assert par["mesmo_comunicante"] is False
    assert len(sob["grupos"]) == 1 and sob["grupos"][0]["piso_sem_sobreposicao"] == 150000.0

    # conferência manual: titular da com#1 (PF1) tem as comunicações 1, 2 e 12
    titular_pf1 = rif.comunicacoes("TESTE")["comunicacoes"][0]["titular"]
    consolidado = {t["titular"]: t for t in sob["por_titular"]}[titular_pf1]
    v1, v2, v12 = (cent(RIF12[i][7]) for i in (0, 1, 11))
    assert consolidado["comunicacoes"] == 3
    assert round(consolidado["soma_bruta"] * 100) == v1 + v2 + v12
    assert round(consolidado["piso_sem_sobreposicao"] * 100) == max(v1, v2) + v12
    assert consolidado["grupos_sobrepostos"] == 1
    # todos os demais titulares: piso == soma bruta
    for t in sob["por_titular"]:
        if t["titular"] != titular_pf1:
            assert t["piso_sem_sobreposicao"] == t["soma_bruta"]

    env = rif.envolvidos("TESTE")
    assert env["envolvidos"] == 7
    por_token = {e["pseudonimo"]: e for e in env["lista"]}
    e1 = por_token[titular_pf1]
    assert e1["n_comunicacoes"] == 4                      # 1, 2, 12 como titular; 6 como destinatário
    assert set(e1["papeis"]) == {"titular", "destinatario"}
    assert e1["como_titular"]["comunicacoes"] == 3
    assert round(e1["como_titular"]["piso_sem_sobreposicao"] * 100) == max(v1, v2) + v12
    assert e1["periodo_inicio"] == "2026-01-05" and e1["periodo_fim"] == "2026-06-30"
    assert env["lista"][0]["n_comunicacoes"] >= env["lista"][-1]["n_comunicacoes"]


def test_resumo(caso_rif):
    rif.parse("TESTE", "DOC-001")
    r = rif.resumo("TESTE")
    assert r["comunicacoes"] == 12 and r["titulares_distintos"] == 7
    assert r["rifs"][0]["numero"] == "67890.2026"
    assert r["por_comunicante"][0]["comunicacoes"] == 4
    assert r["piso_sem_sobreposicao"] < r["soma_bruta_nao_consolidada"]
    assert round((r["soma_bruta_nao_consolidada"] - r["piso_sem_sobreposicao"]) * 100) == cent(RIF12[1][7])
    assert r["pares_sobrepostos"] == 1


def test_parse_idempotente_e_tipo_errado(caso_rif):
    rif.parse("TESTE", "DOC-001")
    assert rif.parse("TESTE", "DOC-001")["comunicacoes"] == 12
    assert rif.comunicacoes("TESTE")["total"] == 12
    with pytest.raises(caso.ErroCaso):
        rif.parse("TESTE", "DOC-999")
    ingestao.ingerir("TESTE", reclassificar="DOC-001", tipo="OUTRO")
    with pytest.raises(caso.ErroCaso):
        rif.parse("TESTE", "DOC-001")
    assert rif.parse("TESTE", "DOC-001", forcar=True)["comunicacoes"] == 12


def test_consultas_sem_parse(caso_rif):
    for fn in (rif.resumo, rif.envolvidos, rif.sobreposicao, rif.comunicacoes):
        with pytest.raises(caso.ErroCaso):
            fn("TESTE")


def test_cli_rif(caso_rif):
    assert cli("rif", "parse", "TESTE", "DOC-001").returncode == 0
    r = cli("rif", "envolvidos", "TESTE")
    assert r.returncode == 0 and json.loads(r.stdout)["envolvidos"] == 7
    r = cli("rif", "sobreposicao", "TESTE", "--md")
    assert r.returncode == 0 and "| titular |" in r.stdout and "com#1" in r.stdout
    r = cli("rif", "comunicacoes", "TESTE", "--tipo", "COA", "--md")
    assert r.returncode == 0 and "COA" in r.stdout
    r = cli("rif", "resumo", "TESTE", "DOC-002")
    assert r.returncode == 1 and "erro" in r.stderr
