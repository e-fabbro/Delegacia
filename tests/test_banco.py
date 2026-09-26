"""F3: `banco importar/integridade/resumo/contrapartes/lancamentos/especie/fracionamento/passagem/circularidade/
cruzar-alvos/linha-tempo` sobre o SIMBA sintético (3 contas, 1 lacuna, ciclo A→B→C→A, conta de passagem B)."""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agencia import banco, caso, ingestao, rif
from tests.fixtures.gerar import CONTAS, SALDO_INICIAL, SIMBA3, PF1, PF2

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CCS, RIF, SIMBA = "DOC-001", "DOC-002", "DOC-003"   # ordem alfabética dos nomes dos brutos
SIMBA_CSV = FIXTURES / "simba_3contas.csv"


def cent(valor_br: str) -> int:
    return int(valor_br.replace(".", "").replace(",", ""))


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True)


def esperado_por_conta():
    e = {}
    for cta, data, hist, valor, nat, od, local in SIMBA3:
        c = e.setdefault(cta, {"n": 0, "C": 0, "D": 0})
        c["n"] += 1
        c[nat] += cent(valor)
    return e


def depositar(raiz_casos, nome: str, texto: str) -> str:
    """Grava um novo bruto sintético no caso, ingere e devolve o doc_id."""
    (raiz_casos / "TESTE" / "00_brutos" / nome).write_text(texto, encoding="utf-8")
    return ingestao.ingerir("TESTE")["novos"][0]["doc_id"]


@pytest.fixture
def caso_banco(raiz_casos):
    caso.novo("TESTE")
    brutos = raiz_casos / "TESTE" / "00_brutos"
    for nome in ("ccs_3contas.xlsx", "rif_12_sintetico.pdf", "simba_3contas.csv"):
        shutil.copy(FIXTURES / nome, brutos / nome)
    r = ingestao.ingerir("TESTE")
    assert {n["doc_id"]: n["tipo"] for n in r["novos"]} == {CCS: "CCS", RIF: "RIF", SIMBA: "SIMBA"}
    banco.importar("TESTE", CCS)
    return raiz_casos / "TESTE"


@pytest.fixture
def importado(caso_banco):
    r = banco.importar("TESTE", SIMBA)
    assert r["transacoes"] == len(SIMBA3) and r["avisos"] == []
    return caso_banco


def test_importar_simba_e_ccs(importado):
    r = banco.importar("TESTE", SIMBA)          # reimportação idempotente
    assert r["transacoes"] == 32
    assert [c["lancamentos"] for c in r["contas"]] == [12, 12, 8]
    assert [c["titular"] for c in r["contas"]] == ["PF-0001", "PF-0002", "PJ-0001"]   # titulares vieram do CCS
    assert (r["periodo_inicio"], r["periodo_fim"]) == ("2026-01-05", "2026-06-30")
    assert banco.lancamentos("TESTE", limite=None)["total"] == 32
    texto = json.dumps(banco.lancamentos("TESTE", limite=None), ensure_ascii=False)
    for claro in (PF1["nome"], PF1["cpf"], PF2["cpf"], CONTAS["A"]["conta"]):
        assert claro not in texto
    with pytest.raises(caso.ErroCaso):
        banco.importar("TESTE", RIF)


def test_criterio_aceite_2_saldo_confere_e_lacuna_detectada(importado):
    r = banco.integridade("TESTE")
    assert r["total_divergencias_saldo"] == 0
    assert all(c["saldo"]["conferido"] for c in r["contas"])
    assert r["total_lacunas"] == 1
    lac = [c for c in r["contas"] if c["lacunas"]][0]
    assert lac["titular"] == "PF-0001"                       # conta A
    assert lac["lacunas"][0] == {"de": "2026-02-05", "ate": "2026-04-15", "dias": 69, "apos": f"[F:{SIMBA}:tx#12]", "antes": f"[F:{SIMBA}:tx#23]"}
    assert banco.integridade("TESTE", lacuna_dias=90)["total_lacunas"] == 0
    # conta D só consta do CCS
    assert [c["titular"] for c in r["contas_ccs_sem_extrato"]] == ["PF-0002"]
    # OD vazio só em espécie/tarifa: nada anômalo
    assert all(c["sem_contraparte"]["n_anomalo"] == 0 for c in r["contas"])
    assert r["duplicidades"] == []


def test_criterio_aceite_6_saldo_adulterado_e_duplicidade(raiz_casos, importado):
    """Cópia do SIMBA com um saldo alterado: `integridade` aponta a divergência e o lançamento."""
    linhas = SIMBA_CSV.read_text(encoding="utf-8").splitlines()
    cab, corpo = linhas[0], linhas[1:]
    campos = corpo[5].split(";")
    campos[8] = "999999,99"                                    # VALOR_SALDO adulterado
    corpo[5] = ";".join(campos)
    novo = depositar(raiz_casos, "zz_simba_adulterado.csv", "\n".join([cab, *corpo]) + "\n")
    banco.importar("TESTE", novo)
    r = banco.integridade("TESTE", doc=novo)
    assert r["total_divergencias_saldo"] >= 1
    c = [c for c in r["contas"] if c["saldo"]["divergencias"]][0]
    assert c["saldo"]["conferido"] is False
    assert c["saldo"]["primeira_divergencia"]["ponteiro"].startswith(f"[F:{novo}:tx#")

    corpo.append(corpo[0])                                     # lançamento repetido
    dup = depositar(raiz_casos, "zz_simba_dup.csv", "\n".join([cab, *corpo]) + "\n")
    banco.importar("TESTE", dup)
    assert banco.integridade("TESTE", doc=dup)["duplicidades"][0]["ocorrencias"] == 2


def test_layout_incompativel_gera_aviso(raiz_casos, importado):
    doc = depositar(raiz_casos, "zz_simba_outro_layout.csv", "COLUNA_X;COLUNA_Y\n1;2\n")
    r = banco.importar("TESTE", doc, layout="simba")
    assert r["transacoes"] == 0 and "colunas ausentes" in r["avisos"][0]


def test_resumo_bate_com_a_fonte(importado):
    r = banco.resumo("TESTE")
    esperado = esperado_por_conta()
    por_titular = {c["titular"]: c for c in r["contas"]}
    for chave, tok in (("A", "PF-0001"), ("B", "PF-0002"), ("C", "PJ-0001")):
        c = por_titular[tok]
        assert c["lancamentos"] == esperado[chave]["n"]
        assert round(c["creditos"] * 100) == esperado[chave]["C"]
        assert round(c["debitos"] * 100) == esperado[chave]["D"]
        assert round(c["saldo_final"] * 100) == SALDO_INICIAL[chave] + esperado[chave]["C"] - esperado[chave]["D"]
    assert round(r["total_creditos"] * 100) == sum(e["C"] for e in esperado.values())
    a = por_titular["PF-0001"]
    assert a["maior_credito"]["valor"] == 50000.0 and a["maior_credito"]["contraparte"] == "PJ-0002"
    assert a["maior_debito"]["valor"] == 20000.0 and a["maior_debito"]["contraparte"] == "PF-0002"
    # filtros
    marco = banco.resumo("TESTE", inicio="2026-03-01", fim="2026-03-31")
    assert all(c["periodo_inicio"] >= "2026-03-01" and c["periodo_fim"] <= "2026-03-31" for c in marco["contas"])
    assert len(banco.resumo("TESTE", conta=a["conta"])["contas"]) == 1


def test_contrapartes(importado):
    r = banco.contrapartes("TESTE", top=3)
    b = [c for c in r["contas"] if c["titular"] == "PF-0002"][0]
    assert b["contrapartes_distintas"] == 6
    assert len(b["contrapartes"]) == 3
    assert b["contrapartes"][0]["contraparte"] == "PF-0004" and b["contrapartes"][0]["creditos"] == 30000.0
    pj2 = [x for x in b["contrapartes"] if x["contraparte"] == "PJ-0002"][0]
    assert pj2["n"] == 3 and pj2["debitos"] == 23800.0 and pj2["titular_conhecido"] is None
    pf1 = [x for x in b["contrapartes"] if x["contraparte"] == "PF-0001"][0]
    assert pf1["titular_conhecido"] == "PF-0001"          # conta A é do caso
    assert banco.lancamentos("TESTE", contraparte="PJ-0003")["total"] == 2


def test_especie_e_fracionamento(importado):
    e = banco.especie("TESTE")
    assert e["operacoes_especie"] == 4
    assert e["total_depositos"] == 29200.0 and e["total_saques"] == 3000.0
    assert e["contas"][0]["titular"] == "PF-0001"
    assert {l["local"] for l in e["contas"][0]["por_local"]} == {"TAGUATINGA DF", "CEILANDIA DF", "BRASILIA DF"}

    f = banco.fracionamento("TESTE")
    assert f["parametros"] == {"limiar": 10000.0, "janela": "dia", "minimo": 2}
    assert f["grupos_suspeitos"] == 1
    g = f["grupos"][0]
    assert g["janela"] == "2026-02-03" and g["operacoes"] == 3 and g["soma"] == 29200.0 and g["especie"] is True
    assert g["ponteiros"] == [f"[F:{SIMBA}:tx#9]", f"[F:{SIMBA}:tx#10]", f"[F:{SIMBA}:tx#11]"]
    assert banco.fracionamento("TESTE", limiar=20000.0)["grupos_suspeitos"] >= 2       # inclui os 2 PIX de 11/03 na conta B
    assert banco.fracionamento("TESTE", limiar=100000.0, janela="semana")["grupos_suspeitos"] == 0


def test_passagem(importado):
    r = banco.passagem("TESTE")
    por_titular = {c["titular"]: c for c in r["contas"]}
    b = por_titular["PF-0002"]
    assert b["perfil_passagem"] is True and b["indice_passagem"] >= 0.95
    assert b["creditos"] == 63500.0 and b["creditos_casados"] == 62700.0    # só o crédito de 500 (25/01) não sai em 48 h
    assert b["giro"] > 5
    assert por_titular["PF-0001"]["perfil_passagem"] is False
    assert por_titular["PJ-0001"]["indice_passagem"] == 0.0
    assert r["contas"][0]["titular"] == "PF-0002"                           # ordenado por índice


def test_circularidade_ciclo_a_b_c_a(importado):
    r = banco.circularidade("TESTE")
    assert r["ciclos"] == 1
    c = r["lista"][0]
    assert c["comprimento"] == 3 and c["ordem_temporal_coerente"] is True
    assert c["valor_minimo_no_ciclo"] == 15000.0
    passos = [(p["de"], p["para"], p["primeira"], p["total"]) for p in c["passos"]]
    assert len({p[0] for p in passos}) == 3
    assert any(p[2] == "2026-01-07" and p[3] == 20000.0 for p in passos)     # A→B
    assert any(p[2] == "2026-01-08" and p[3] == 19900.0 for p in passos)     # B→C
    assert any(p[2] == "2026-04-15" and p[3] == 15000.0 for p in passos)     # C→A
    assert all(len(p["ponteiros"]) == 2 for p in c["passos"])                # cada passo consta dos dois extratos

    ent = banco.circularidade("TESTE", nivel="entidade")
    assert ent["ciclos"] == 1 and set(ent["lista"][0]["nos"]) == {"PF-0001", "PF-0002", "PJ-0001"}
    assert banco.circularidade("TESTE", incluir_terceiros=True)["ciclos"] > 1
    assert banco.circularidade("TESTE", fim="2026-03-31")["ciclos"] == 0     # C→A só em abril


def test_cruzar_alvos(importado):
    r = banco.cruzar_alvos("TESTE")
    pares = {(p["de"], p["para"]): p for p in r["pares"]}
    assert set(pares) == {("PF-0001", "PF-0002"), ("PF-0002", "PJ-0001"), ("PJ-0001", "PF-0001")}
    ab = pares[("PF-0001", "PF-0002")]
    assert ab["n"] == 2 and ab["lancamentos_espelhados"] == 1 and ab["n_transferencias_unicas"] == 1 and ab["total"] == 40000.0
    assert r["alvos"]["contas_do_caso"]
    with pytest.raises(caso.ErroCaso):
        banco.cruzar_alvos("TESTE", fonte="rif")                             # RIF ainda não parseado

    rif.parse("TESTE", RIF)
    r2 = banco.cruzar_alvos("TESTE", fonte="rif")
    assert "rif" in r2["alvos"] and len(r2["pares"]) > len(r["pares"])
    assert ("PF-0002", "PJ-0003") in {(p["de"], p["para"]) for p in r2["pares"]}


def test_linha_tempo(importado):
    r = banco.linha_tempo("TESTE")
    assert [s["periodo"] for s in r["serie"]] == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert sum(s["lancamentos"] for s in r["serie"]) == 32
    assert r["picos"] == ["2026-01"]
    assert len(banco.linha_tempo("TESTE", granularidade="semana")["serie"]) > 6
    conta_a = [c for c in banco.resumo("TESTE")["contas"] if c["titular"] == "PF-0001"][0]["conta"]
    assert len(banco.linha_tempo("TESTE", granularidade="dia", conta=conta_a)["serie"]) == 10   # 12 lançamentos em 10 dias distintos


def test_salvar_agregado_vira_fonte(importado):
    r = cli("banco", "passagem", "TESTE", "--salvar")
    assert r.returncode == 0
    salvo = json.loads(r.stdout)["salvo"]
    assert salvo["agg"] == "banco_passagem_horas48"
    assert salvo["ponteiros"] == [f"[F:{SIMBA}:agg#banco_passagem_horas48]"]
    arq = importado / salvo["arquivo"]
    assert arq.is_file()
    dados = json.loads(arq.read_text(encoding="utf-8"))
    assert dados["resultado"]["contas"][0]["indice_passagem"] >= 0.95
    con = sqlite3.connect(importado / "caso.db")
    assert con.execute("select count(*) from agregados where nome='banco_passagem_horas48'").fetchone()[0] == 1
    con.close()


def test_cli_banco(caso_banco):
    r = cli("banco", "integridade", "TESTE")
    assert r.returncode == 1 and "importar" in r.stderr
    assert cli("banco", "importar", "TESTE", SIMBA).returncode == 0
    for cmd in (["integridade", "--md"], ["resumo", "--md"], ["contrapartes", "--top", "5", "--md"], ["especie"],
                ["fracionamento", "--limiar", "10000"], ["passagem", "--horas", "48"], ["circularidade", "--nivel", "entidade"],
                ["cruzar-alvos"], ["linha-tempo", "--granularidade", "mes", "--md"], ["lancamentos", "--conta", "CT-0001", "--limite", "5"]):
        r = cli("banco", cmd[0], "TESTE", *cmd[1:])
        assert r.returncode == 0, (cmd, r.stderr)
    assert "| conta |" in cli("banco", "resumo", "TESTE", "--md").stdout
