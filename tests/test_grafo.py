"""F4: `grafo construir/centrais/exportar` e `linha-tempo integrada`."""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agencia import banco, caso, grafo, ingestao, integracao, rif
from tests.fixtures.gerar import PF1, PF2, PJ1, CONTAS

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CCS, RIF, SIMBA = "DOC-001", "DOC-002", "DOC-003"


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True)


@pytest.fixture
def caso_fontes(raiz_casos):
    caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    for nome in ("ccs_3contas.xlsx", "rif_12_sintetico.pdf", "simba_3contas.csv"):
        shutil.copy(FIXTURES / nome, d / "00_brutos" / nome)
    ingestao.ingerir("TESTE")
    banco.importar("TESTE", CCS)
    banco.importar("TESTE", SIMBA)
    rif.parse("TESTE", RIF)
    return d


def achado(d, agente, id_, entidades, periodo=None):
    pasta = d / "03_analises" / agente
    pasta.mkdir(parents=True, exist_ok=True)
    a = {"id": id_, "agente": agente, "rotulo": "FATO", "relevancia": "alta", "enunciado": "Achado sintético para o grafo.",
         "entidades": entidades, "fontes": [{"doc_id": SIMBA, "localizador": "tx#1"}]}
    if periodo:
        a["periodo"] = periodo
    with (pasta / "achados.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(a) + "\n")


def test_construir(caso_fontes):
    r = grafo.construir("TESTE")
    assert r["nos"] == 11 and r["componentes"] == 1
    assert r["por_tipo"] == {"transferencia": 16, "rif": 13, "ccs": 5}
    assert r["nos_por_tipo"] == {"PF": 4, "PJ": 3, "CT": 4}
    con = sqlite3.connect(caso_fontes / "caso.db")
    con.row_factory = sqlite3.Row
    v = {(l["origem"], l["destino"], l["tipo"]): dict(l) for l in con.execute("select * from vinculos")}
    # A→B: 20.000 em 07/01, registrado nos dois extratos (2 lançamentos, mesma transferência)
    ab = v[("PF-0001", "PF-0002", "transferencia")]
    assert ab["total_centavos"] == 4000000 and ab["n"] == 2 and ab["primeira"] == ab["ultima"] == "2026-01-07"
    assert json.loads(ab["fontes"]) == [f"[F:{SIMBA}:tx#2]", f"[F:{SIMBA}:tx#3]"]
    # RIF: titular da com#1 -> remetente
    r1 = v[("PF-0001", "PJ-0001", "rif")]
    assert json.loads(r1["papeis"]) == ["remetente"] and f"[F:{RIF}:com#1]" in json.loads(r1["fontes"])
    # CCS: pessoa -> conta com papel
    assert json.loads(v[("PF-0002", "CT-0003", "ccs")]["papeis"]) == ["procurador"]
    assert json.loads(v[("PF-0002", "CT-0003", "ccs")]["fontes"]) == [f"[F:{CCS}:ccs#5]"]
    # entidades sincronizadas
    assert con.execute("select count(*) from entidades where pseudonimo like 'CT-%'").fetchone()[0] >= 4
    con.close()


def test_construir_com_achados(caso_fontes):
    achado(caso_fontes, "analista-rif", "RIF-001", ["PF-0001", "PJ-0003", "TEL-0001"])
    r = grafo.construir("TESTE")
    assert r["achados_lidos"] == 1 and r["por_tipo"]["achado"] == 3 and r["nos"] == 12
    assert grafo.construir("TESTE", sem_achados=True)["nos"] == 11
    assert grafo.construir("TESTE")["arestas"] == 37       # reconstrução idempotente


def test_centrais(caso_fontes):
    grafo.construir("TESTE")
    r = grafo.centrais("TESTE", top=3)
    assert r["nos"] == 11 and r["componentes"] == 1
    assert [x["no"] for x in r["centrais"]] == ["PF-0002", "PF-0001", "PJ-0001"]
    pf2 = r["centrais"][0]
    assert pf2["grau"] == 9 and pf2["ponte"] and pf2["convergencia"] and set(pf2["fontes"]) == {"ccs", "rif", "transferencia"}
    assert set(r["pontes"]) == {"PF-0001", "PF-0002"}
    assert "PF-0002" in r["convergentes"] and "CT-0004" not in r["convergentes"]
    assert r["por_intermediacao"][0] == "PF-0002"
    so_rif = grafo.centrais("TESTE", tipo="rif")
    assert so_rif["arestas"] < r["arestas"] and all("rif" in x["fontes"] for x in so_rif["centrais"])
    with pytest.raises(caso.ErroCaso):
        grafo.centrais("TESTE", tipo="achado")


def test_exportar(caso_fontes):
    grafo.construir("TESTE")
    r = grafo.exportar("TESTE", formato="todos")
    assert set(r["arquivos"]) == {"html", "json", "graphml"}
    html = (caso_fontes / "04_produtos" / "grafo.html").read_text(encoding="utf-8")
    assert "<svg" in html and html.count('class="no"') == 11 and "<script src=" not in html and "http://" not in html.replace("http://www.w3.org", "")
    for claro in (PF1["nome"], PF2["nome"], PJ1["nome"], PF1["cpf"], CONTAS["A"]["conta"]):
        assert claro not in html
    dados = json.loads((caso_fontes / "04_produtos" / "grafo.json").read_text(encoding="utf-8"))
    assert len(dados["nos"]) == 11 and len(dados["arestas"]) == 34
    assert all(0 <= n["x"] <= 1000 and 0 <= n["y"] <= 1000 for n in dados["nos"])
    assert (caso_fontes / "04_produtos" / "grafo.graphml").stat().st_size > 0


def test_grafo_vazio(raiz_casos):
    caso.novo("VAZIO")
    with pytest.raises(caso.ErroCaso):
        grafo.centrais("VAZIO")
    r = grafo.construir("VAZIO")
    assert r["nos"] == 0 and r["arestas"] == 0


def test_linha_tempo_integrada(caso_fontes):
    r = integracao.integrada("TESTE")
    assert r["por_fonte"] == {"bancario": 32, "rif": 24, "ccs": 5}
    assert r["periodo_inicio"] == "2019-05-10" and r["periodo_fim"] == "2026-06-30"
    assert r["picos_bancarios"] == ["2026-01"]
    assert r["periodos_convergentes"] == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    jan = [s for s in r["serie"] if s["periodo"] == "2026-01"][0]
    assert jan["por_fonte"] == {"bancario": 8, "rif": 3} and jan["movimentacao"] == 139300.0
    assert all(m["fonte"] in ("rif", "ccs") for m in r["marcos"]) and len(r["marcos"]) == 29
    # filtro por entidade e por período
    pf1 = integracao.integrada("TESTE", entidade="PF-0001", inicio="2026-01-01")
    assert pf1["eventos"] < r["eventos"] and pf1["periodo_inicio"] >= "2026-01-01"
    assert all("PF-0001" in m["entidades"] or m["fonte"] == "bancario" for m in pf1["marcos"])
    assert len(integracao.integrada("TESTE", granularidade="semana")["serie"]) > len(r["serie"])
    with pytest.raises(caso.ErroCaso):
        integracao.integrada("TESTE", inicio="2030-01-01")


def test_linha_tempo_com_achado(caso_fontes):
    achado(caso_fontes, "analista-bancario", "BAN-001", ["PF-0001"], periodo={"inicio": "2026-02-03", "fim": "2026-02-03"})
    r = integracao.integrada("TESTE", granularidade="dia", inicio="2026-02-03", fim="2026-02-03")
    assert r["por_fonte"]["achado"] == 1
    assert any(m["fonte"] == "achado" and m["ponteiro"] == "achado:analista-bancario:BAN-001" for m in r["marcos"])


def test_cli_grafo_e_linha_tempo(caso_fontes):
    assert cli("grafo", "construir", "TESTE").returncode == 0
    r = cli("grafo", "centrais", "TESTE", "--top", "5", "--md")
    assert r.returncode == 0 and "| no |" in r.stdout
    r = cli("grafo", "exportar", "TESTE")
    assert r.returncode == 0 and json.loads(r.stdout)["arquivos"] == {"html": "04_produtos/grafo.html"}
    r = cli("linha-tempo", "integrada", "TESTE", "--md")
    assert r.returncode == 0 and "| periodo |" in r.stdout
