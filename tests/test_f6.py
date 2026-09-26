"""F6: `tel`, `soc`, `cripto` + integração (ponteiros ev#/qsa#/mov#, grafo, linha do tempo, matrizes)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest

from agencia import achados, banco, caso, cripto, grafo, ingestao, integracao, matrizes, soc, tel
from tests.fixtures.gerar import CARTEIRA_1, CARTEIRA_2, PF1, PF2, PF3, PJ1, PJ3, ENDERECO_X

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CCS, CRIPTO, ERB, SIMBA, SOC, TELE = "DOC-001", "DOC-002", "DOC-003", "DOC-004", "DOC-005", "DOC-006"


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True)


@pytest.fixture
def caso_f6(raiz_casos):
    caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    for nome in ("ccs_3contas.xlsx", "cripto_sintetico.csv", "erb_sintetica.csv", "simba_3contas.csv", "societario_sintetico.xlsx", "telematica_sintetica.csv"):
        shutil.copy(FIXTURES / nome, d / "00_brutos" / nome)
    r = ingestao.ingerir("TESTE")
    assert {n["doc_id"]: n["tipo"] for n in r["novos"]} == {CCS: "CCS", CRIPTO: "CRIPTO", ERB: "ERB", SIMBA: "SIMBA", SOC: "SOCIETARIO", TELE: "TELEMATICA"}
    banco.importar("TESTE", CCS)
    banco.importar("TESTE", SIMBA)
    return d


def test_pseudonimizacao_das_novas_fontes(caso_f6):
    from agencia import cofre
    assert cofre.vazamento("TESTE")["total"] == 0
    qsa = (caso_f6 / "02_extraido" / f"{SOC}.tabelas" / "QSA.csv").read_text(encoding="utf-8")
    assert PF3["nome"] not in qsa and ENDERECO_X not in qsa and "END-0001" in qsa and "PF-0003" in qsa
    cr = (caso_f6 / "02_extraido" / f"{CRIPTO}.tabelas" / "dados.csv").read_text(encoding="utf-8")
    assert PJ3["nome"] not in cr and CARTEIRA_1 in cr and "CT-0002" in cr          # carteira e TXID ficam em claro
    te = (caso_f6 / "02_extraido" / f"{TELE}.tabelas" / "dados.csv").read_text(encoding="utf-8")
    assert PF1["email"] not in te and "177.10.20.30" in te and "41234" in te and "acct-7781" in te


# ---------- tel ----------

def test_tel_importar_e_normalizar(caso_f6):
    r = tel.importar("TESTE", TELE)
    assert r["eventos"] == 14 and r["fuso"] == "UTC" and r["origem_fuso"] == "nome_da_coluna" and r["avisos"] == []
    assert r["identificadores"] == ["EML-0001", "EML-0002"]
    r = tel.importar("TESTE", ERB)
    assert r["eventos"] == 6 and r["fuso"] == "America/Sao_Paulo" and r["origem_fuso"].startswith("presuncao")
    assert r["periodo_utc_inicio"] == "2026-03-10T14:05:00"                      # 11:05 local -> 14:05 UTC
    n = tel.normalizar("TESTE")
    assert n["presumidos"] == [ERB] and {f["doc_id"]: f["fuso"] for f in n["fontes"]} == {TELE: "UTC", ERB: "America/Sao_Paulo"}
    n = tel.normalizar("TESTE", doc=ERB, fuso="-04:00")
    assert {f["doc_id"]: f["origem_fuso"] for f in n["fontes"]}[ERB] == "manual" and n["presumidos"] == []
    assert tel.janela("TESTE", "2026-03-10 15:05:00", "2026-03-10 15:05:00")["eventos"] == 3   # 2 registros ERB (11:05 em UTC-4) + 1 logout telemático às 15:05Z
    with pytest.raises(caso.ErroCaso):
        tel.normalizar("TESTE", fuso="UTC")
    with pytest.raises(caso.ErroCaso):
        tel.importar("TESTE", ERB, fuso="Marte/Olympus")
    with pytest.raises(caso.ErroCaso):
        tel.importar("TESTE", SIMBA)


def test_tel_ips_cgnat_e_compartilhado(caso_f6):
    tel.importar("TESTE", TELE)
    r = tel.ips("TESTE")
    assert r["pares_identificador_ip"] == 4 and r["eventos_com_ip"] == 14
    assert r["ips_compartilhados"] == [{"ip": "177.10.20.30", "identificadores": ["EML-0001", "EML-0002"]}]
    dil = r["diligencias_porta"]
    assert len(dil) == 1 and dil[0]["ip"] == "100.72.5.9" and dil[0]["eventos_sem_porta"] == 4 and dil[0]["identificador"] == "EML-0001"
    por_ip = {(x["identificador"], x["ip"]): x for x in r["lista"]}
    assert por_ip[("EML-0001", "177.10.20.30")]["portas"] == [41234, 50001] and por_ip[("EML-0001", "177.10.20.30")]["cgnat"] is False
    assert por_ip[("EML-0001", "100.72.5.9")]["cgnat"] and por_ip[("EML-0001", "100.72.5.9")]["ponteiros"][0] == f"[F:{TELE}:ev#4]"
    assert tel.ips("TESTE", identificador="EML-0002")["pares_identificador_ip"] == 2


def test_tel_sessoes_vinculados_e_erb(caso_f6):
    tel.importar("TESTE", TELE)
    tel.importar("TESTE", ERB)
    r = tel.sessoes("TESTE")
    assert r["parametros"]["intervalo_min"] == 30
    por_id = {i["identificador"]: i for i in r["identificadores"]}
    assert por_id["EML-0001"]["sessoes"] == 4 and por_id["EML-0002"]["sessoes"] == 3
    assert {v["identificador"] for v in por_id["EML-0001"]["vinculados"]} == {"EML-0002", "TEL-0001"}   # e-mail e telefone de recuperação
    assert {v["identificador"] for v in por_id["EML-0002"]["vinculados"]} == {"EML-0001", "TEL-0002"}   # troca de senha com e-mail de PF1
    comp = {c["vinculado"]: c["identificadores"] for c in r["identificadores_compartilhados"]}
    assert comp["TEL-0003"] == ["TEL-0001", "TEL-0002"]
    s1 = [s for s in r["lista"] if s["identificador"] == "EML-0001" and s["ip"] == "100.72.5.9"]
    assert len(s1) == 2                                                          # logout 32 min depois abre outra sessão
    assert s1[0]["eventos"] == 3 and s1[0]["inicio_utc"] == "2026-03-10T14:20:11" and s1[0]["fim_utc"] == "2026-03-10T14:33:02" and s1[0]["duracao_min"] == 12.8
    assert s1[0]["portas"] == [] and s1[0]["dispositivos"][0].startswith("Mozilla")
    erbs = r["coincidencias_erb"]
    assert erbs and all(c["erb"] == "ERB-101" and c["identificadores"] == ["TEL-0001", "TEL-0002"] for c in erbs)
    assert min(c["minutos"] for c in erbs) == 1.5
    assert tel.sessoes("TESTE", intervalo_min=5)["sessoes"] > r["sessoes"]


def test_tel_janela_do_fato(caso_f6):
    tel.importar("TESTE", TELE)
    tel.importar("TESTE", ERB)
    r = tel.janela("TESTE", "2026-03-10 14:25:00", "2026-03-10 14:40:00")
    assert r["janela_utc"] == ["2026-03-10T14:25:00", "2026-03-10T14:40:00"] and r["eventos"] == 6
    pres = {p["identificador"]: p for p in r["identificadores_presentes"]}
    assert set(pres) == {"EML-0001", "EML-0002", "TEL-0001", "TEL-0002"}
    assert pres["EML-0001"]["ips"] == ["100.72.5.9"] and pres["EML-0002"]["tipos"] == ["ACESSO", "TROCA_SENHA"]
    assert pres["TEL-0001"]["erbs"] == ["ERB-101"] and pres["TEL-0001"]["municipios"] == ["BRASILIA"]
    assert len(r["coincidencias_erb"]) == 1 and r["coincidencias_erb"][0]["minutos"] == 1.5
    local = tel.janela("TESTE", "10/03/2026 11:25:00", "10/03/2026 11:40:00", fuso_entrada="America/Sao_Paulo")
    assert local["janela_utc"] == r["janela_utc"] and local["eventos"] == 6
    assert tel.janela("TESTE", "2026-03-20 00:00:00", "2026-03-20 23:59:59")["eventos"] == 0
    with pytest.raises(caso.ErroCaso):
        tel.janela("TESTE", "2026-03-10 15:00:00", "2026-03-10 14:00:00")


# ---------- soc ----------

def test_soc_importar_qsa_compartilhados(caso_f6):
    r = soc.importar("TESTE", SOC)
    assert r["pjs"] == 3 and r["vinculos_qsa"] == 5 and r["avisos"] == []
    q = soc.qsa("TESTE")
    por_pj = {p["pj"]: p for p in q["lista"]}
    pj1 = por_pj["PJ-0001"]
    assert pj1["abertura"] == "2023-08-15" and pj1["capital"] == 10000.0 and pj1["cnae"] == "4530-7/03" and pj1["endereco"] == "END-0001"
    assert pj1["socios_ativos"] == 1 and pj1["administradores"] == ["PF-0001"]
    assert pj1["trocas_societarias"] == [{"socio": "PF-0003", "saida": "2026-02-01", "ponteiro": f"[F:{SOC}:qsa#2]"}]
    assert por_pj["PJ-0002"]["administradores"] == ["PF-0003"] and por_pj["PJ-0003"]["capital"] == 1000000.0
    assert soc.qsa("TESTE", pj="PJ-0002")["pjs"] == 1
    c = soc.compartilhados("TESTE")
    assert [(s["socio"], s["pjs"]) for s in c["socios_compartilhados"]] == [("PF-0003", ["PJ-0001", "PJ-0002"]), ("PF-0004", ["PJ-0002", "PJ-0003"])]
    assert c["enderecos_compartilhados"] == [{"endereco": "END-0001", "pjs": ["PJ-0001", "PJ-0002"]}]
    with pytest.raises(caso.ErroCaso):
        soc.importar("TESTE", SIMBA)


def test_soc_cruzar_bancario(caso_f6):
    soc.importar("TESTE", SOC)
    r = soc.cruzar_bancario("TESTE")
    por_pj = {p["pj"]: p for p in r["pjs"]}
    pj1 = por_pj["PJ-0001"]
    assert pj1["contas"] == ["CT-0003"] and pj1["creditos"] == 47900.0 and pj1["razao_creditos_capital"] == 4.79 and pj1["meses_abertura_ate_movimentacao"] == 29
    assert pj1["indicios"] == []
    assert por_pj["PJ-0002"]["contas"] == [] and any("pico" in i for i in por_pj["PJ-0002"]["indicios"])
    assert "PJ-0002: sem conta com extrato no caso" in r["avisos"] and r["picos_bancarios"] == ["2026-01"]
    assert r["pjs"][0]["pj"] == "PJ-0002"                                    # mais indícios primeiro


# ---------- cripto ----------

def test_cripto_importar_fluxos(caso_f6):
    r = cripto.importar("TESTE", CRIPTO)
    assert r["movimentacoes"] == 10 and r["avisos"] == [] and r["exchanges"] == ["PJ-0003"] and r["clientes"] == ["PF-0002", "PF-0003"]
    assert r["por_tipo"] == {"deposito_fiat": 2, "compra": 2, "saque_cripto": 3, "deposito_cripto": 1, "venda": 1, "saque_fiat": 1}
    f = cripto.fluxos("TESTE")
    por_cli = {c["cliente"]: c for c in f["contas"]}
    c2 = por_cli["PF-0002"]
    assert c2["deposito_fiat"] == {"n": 2, "valor_brl": 19300.0, "contrapartes_bancarias": ["CT-0002"], "ponteiros": [f"[F:{CRIPTO}:mov#1]", f"[F:{CRIPTO}:mov#4]"]}
    ativos = {a["ativo"]: a for a in c2["ativos"]}
    assert ativos["USDT"]["compra"]["quantidade"] == "2950" and ativos["USDT"]["saque_cripto"]["quantidade"] == "2940" and ativos["USDT"]["saque_cripto"]["redes"] == ["TRON"]
    assert ativos["BTC"]["saque_cripto"]["quantidade"] == "0.0069"
    assert c2["padrao"] == "fiat→cripto→saída on-chain" and c2["saques_cripto_para_enderecos"] == sorted([CARTEIRA_1, CARTEIRA_2])
    c3 = por_cli["PF-0003"]
    assert c3["saque_fiat"]["valor_brl"] == 5050.0 and c3["padrao"] == "entrada on-chain→fiat"
    assert f["total_deposito_fiat"] == 19300.0 and f["total_saque_fiat"] == 5050.0
    assert cripto.fluxos("TESTE", cliente="PF-0003")["contas"][0]["cliente"] == "PF-0003"
    with pytest.raises(caso.ErroCaso):
        cripto.importar("TESTE", SIMBA)


def test_cripto_enderecos_e_exchanges(caso_f6):
    cripto.importar("TESTE", CRIPTO)
    e = cripto.enderecos("TESTE")
    assert e["enderecos"] == 2 and e["compartilhados"] == [CARTEIRA_1] and e["recorrentes"] == [CARTEIRA_1]
    a1 = e["lista"][0]
    assert a1["endereco"] == CARTEIRA_1 and a1["clientes"] == ["PF-0002", "PF-0003"] and a1["movimentacoes"] == 3
    assert a1["ativos"] == [{"ativo": "USDT", "quantidade": "4440", "valor_brl": 22634.0}] and a1["tipos"] == {"saque_cripto": 2, "deposito_cripto": 1}
    assert len(e["diligencias_rastreio"]) == 1
    x = cripto.exchanges("TESTE")
    assert x["exchanges"] == [{"exchange": "PJ-0003", "pj_do_caso": False, "clientes": ["PF-0002", "PF-0003"], "movimentacoes": 10,
                               "deposito_fiat": 19300.0, "saque_fiat": 5050.0, "docs": [CRIPTO]}]
    lig = {l["ponteiro"]: l for l in x["ligacoes_fiat"]}
    d1 = lig[f"[F:{CRIPTO}:mov#1]"]
    assert d1["conta_bancaria"] == "CT-0002" and d1["titular_conta"] == "PF-0002" and d1["cliente_e_titular"] is True and d1["conta_do_caso"] is True
    assert d1["lancamento_bancario"]["ponteiro"] == f"[F:{SIMBA}:tx#17]" and d1["lancamento_bancario"]["contraparte"] == "PJ-0003"
    assert lig[f"[F:{CRIPTO}:mov#9]"]["lancamento_bancario"] is None and lig[f"[F:{CRIPTO}:mov#9]"]["titular_conta"] is None
    assert x["ligadas_a_lancamento"] == 2 and x["diligencias"][0]["tipo"] == "OFICIO_EXCHANGE"
    soc.importar("TESTE", SOC)
    assert cripto.exchanges("TESTE")["exchanges"][0]["pj_do_caso"] is True


# ---------- integração ----------

def test_ponteiros_novos_resolvem_e_conferem(caso_f6):
    tel.importar("TESTE", TELE)
    soc.importar("TESTE", SOC)
    cripto.importar("TESTE", CRIPTO)
    pasta = caso_f6 / "03_analises" / "integrador-vinculos"
    pasta.mkdir(parents=True)
    ok = [
        {"id": "INT-001", "agente": "integrador-vinculos", "rotulo": "FATO", "relevancia": "alta", "entidades": ["EML-0001"],
         "enunciado": "EML-0001 acessou de IP em faixa CGNAT sem porta lógica às 14:31:50 UTC de 10/03/2026.",
         "periodo": {"inicio": "2026-03-10", "fim": "2026-03-10"}, "fontes": [{"doc_id": TELE, "localizador": "ev#5"}]},
        {"id": "INT-002", "agente": "integrador-vinculos", "rotulo": "FATO", "relevancia": "media", "entidades": ["PF-0003", "PJ-0001"],
         "enunciado": "PF-0003 deixou o quadro de PJ-0001 em 01/02/2026.", "fontes": [{"doc_id": SOC, "localizador": "qsa#2"}],
         "valores": [{"valor": 10000.0, "origem": "soc qsa (capital social)"}]},
        {"id": "INT-003", "agente": "integrador-vinculos", "rotulo": "FATO", "relevancia": "alta", "entidades": ["PF-0002", "CT-0002"],
         "enunciado": "PF-0002 depositou R$ 15.000,00 na exchange a partir de CT-0002.",
         "valores": [{"valor": 15000.0, "origem": "cripto exchanges"}], "fontes": [{"doc_id": CRIPTO, "localizador": "mov#1"}, {"doc_id": SIMBA, "localizador": "tx#17"}]},
    ]
    (pasta / "achados.jsonl").write_text("\n".join(json.dumps(a) for a in ok) + "\n", encoding="utf-8")
    assert achados.validar("TESTE")["ok"]
    r = achados.verificar("TESTE")
    assert r["erros"] == 0, r
    ruim = json.loads(json.dumps(ok))
    ruim[2]["valores"][0]["valor"] = 15001.0
    ruim[0]["fontes"][0]["localizador"] = "ev#99"
    ruim[1]["fontes"][0]["localizador"] = "qsa#9"
    (pasta / "achados.jsonl").write_text("\n".join(json.dumps(a) for a in ruim) + "\n", encoding="utf-8")
    r = achados.verificar("TESTE")
    erros = " | ".join(e["erro"] for e in r["por_agente"][0]["lista_erros"])
    assert "ev#99" in erros and "qsa#9" in erros and "15001.0" in erros and "10000.0 sem fonte" in erros and r["erros"] == 4


def test_grafo_linha_tempo_matrizes_com_f6(caso_f6):
    tel.importar("TESTE", TELE)
    tel.importar("TESTE", ERB)
    soc.importar("TESTE", SOC)
    cripto.importar("TESTE", CRIPTO)
    g = grafo.construir("TESTE")
    assert g["por_tipo"] == {"transferencia": 16, "ccs": 5, "telematico": 8, "societario": 8, "cripto": 3}
    assert g["nos_por_tipo"]["EML"] == 2 and g["nos_por_tipo"]["TEL"] == 3 and g["nos_por_tipo"]["END"] == 2
    c = grafo.centrais("TESTE", top=30)
    pf3 = [x for x in c["centrais"] if x["no"] == "PF-0003"][0]
    assert {"transferencia", "societario", "cripto"} <= set(pf3["fontes"]) and pf3["convergencia"]
    assert grafo.centrais("TESTE", tipo="telematico")["nos"] == 5
    lt = integracao.integrada("TESTE")
    assert lt["por_fonte"] == {"societario": 9, "ccs": 5, "bancario": 32, "telematico": 20, "cripto": 10}
    dia = integracao.integrada("TESTE", granularidade="dia", inicio="2026-03-10", fim="2026-03-10")
    assert dia["serie"][0]["por_fonte"] == {"bancario": 1, "telematico": 12}
    marcos_soc = [m for m in lt["marcos"] if m["fonte"] == "societario"]
    assert any(m["tipo"] == "saida_socio" and m["ponteiro"] == f"[F:{SOC}:qsa#2]" for m in marcos_soc)
    m = matrizes.matrizes("TESTE")
    for nome in ("Tel_IPs", "Tel_Sessoes", "Tel_Vinculados", "Tel_ERB_Coincidencias", "PJ", "PJ_QSA", "PJ_x_Bancario", "Cripto_Fluxos", "Cripto_Enderecos", "Cripto_Ligacoes_Fiat"):
        assert m["planilhas"].get(nome), nome
    wb = openpyxl.load_workbook(caso_f6 / "04_produtos" / "matrizes.xlsx")
    tudo = " ".join(str(c.value) for w in wb.worksheets for row in w.iter_rows() for c in row if c.value is not None)
    for claro in (PF1["email"], PF2["cpf"], PJ1["nome"], ENDERECO_X):
        assert claro not in tudo
    assert CARTEIRA_1 in tudo and "177.10.20.30" in tudo


def test_cli_f6(caso_f6):
    assert cli("tel", "importar", "TESTE", TELE).returncode == 0
    assert cli("tel", "importar", "TESTE", ERB, "--fuso", "America/Sao_Paulo").returncode == 0
    assert cli("soc", "importar", "TESTE", SOC).returncode == 0
    assert cli("cripto", "importar", "TESTE", CRIPTO).returncode == 0
    for grupo, cmd in (("tel", ["normalizar", "--md"]), ("tel", ["ips", "--md"]), ("tel", ["sessoes"]),
                       ("tel", ["janela", "--inicio", "2026-03-10 14:00:00", "--fim", "2026-03-10 15:00:00", "--fuso-entrada", "UTC", "--md"]),
                       ("soc", ["qsa", "--md"]), ("soc", ["compartilhados"]), ("soc", ["cruzar-bancario", "--md"]),
                       ("cripto", ["fluxos", "--md"]), ("cripto", ["enderecos"]), ("cripto", ["exchanges", "--tolerancia-dias", "3"])):
        r = cli(grupo, cmd[0], "TESTE", *cmd[1:])
        assert r.returncode == 0, (grupo, cmd, r.stderr)
    r = cli("tel", "janela", "TESTE", "--inicio", "x", "--fim", "y")
    assert r.returncode == 1 and "erro" in r.stderr
