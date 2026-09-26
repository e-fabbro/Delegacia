"""Ponta a ponta: caso novo → ingestão → todas as importações e análises → achados → grafo → produto →
matrizes → render → handoff → arquivamento. Um só caso sintético com as seis fontes; nada lê 00_brutos/_cofre
fora do pacote agencia. Serve de fumaça para regressões entre módulos."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import docx
import pytest

from agencia import achados, arquivo, banco, caso, cofre, cripto, grafo, handoff, ingestao, integracao, matrizes, render, rif, soc, tel
from tests.fixtures.gerar import PF1, PF2, PF3, PJ1, PJ3

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRUTOS = ["ccs_3contas.xlsx", "cripto_sintetico.csv", "erb_sintetica.csv", "rif_12_sintetico.pdf", "simba_3contas.csv",
          "societario_sintetico.xlsx", "telematica_sintetica.csv"]
CCS, CRIPTO, ERB, RIF, SIMBA, SOC, TELE = (f"DOC-{i:03d}" for i in range(1, 8))
RE_TOKEN = re.compile(r"\b(PF|PJ|CT|TEL|EML|PIX|END)-\d{4}\b")
SENHA = "senha-do-pipeline-de-teste-2026"

PRODUTO = """# Informação de Análise — PIPE

## 1. Objeto
Caso PIPE, pergunta investigativa sintética.

## 4. Análise por fonte
- **FATO** Segundo o comunicante, PF-0001 recebeu R$ 150.000,00 de PJ-0001 entre 05/01/2026 e 20/02/2026 [F:DOC-004:com#1].
- **FATO** PF-0001 transferiu R$ 20.000,00 a PF-0002 em 07/01/2026 [F:DOC-005:tx#2].
- **FATO** PF-0002 depositou R$ 15.000,00 na exchange PJ-0003 a partir de CT-0002 em 11/03/2026 [F:DOC-002:mov#1] [F:DOC-005:tx#17].
- **INFERÊNCIA** A conta CT-0002 tem perfil de passagem (índice 0,987) [F:DOC-005:agg#banco_passagem_horas48].
- **FATO** PF-0003 deixou o quadro de PJ-0001 em 01/02/2026 [F:DOC-006:qsa#2].
- **FATO** EML-0001 acessou de 100.72.5.9 sem porta lógica às 14:31:50 UTC de 10/03/2026 [F:DOC-007:ev#5].

## 7. Hipóteses e limitações
- **HIPÓTESE** PF-0003 pode operar como conta de passagem para PF-0002 [F:DOC-004:com#8].
"""


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True)


def gravar_achados(d: Path, agente: str, lista: list[dict]) -> None:
    pasta = d / "03_analises" / agente
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "achados.jsonl").write_text("\n".join(json.dumps(a, ensure_ascii=False) for a in lista) + "\n", encoding="utf-8")


def test_pipeline_completo(raiz_casos, monkeypatch, tmp_path):
    # 1. caso e ingestão
    caso.novo("PIPE")
    d = raiz_casos / "PIPE"
    for nome in BRUTOS:
        shutil.copy(FIXTURES / nome, d / "00_brutos" / nome)
    r = ingestao.ingerir("PIPE")
    assert r["documentos"] == 7 and r["alertas"] == []
    assert {n["doc_id"]: n["tipo"] for n in r["novos"]} == {CCS: "CCS", CRIPTO: "CRIPTO", ERB: "ERB", RIF: "RIF", SIMBA: "SIMBA", SOC: "SOCIETARIO", TELE: "TELEMATICA"}
    assert cofre.vazamento("PIPE")["total"] == 0
    assert caso.estado("PIPE")["fase"] == "ingestao"

    # 2. importações (ordem: CCS antes de SIMBA; societário antes de cripto exchanges)
    assert banco.importar("PIPE", CCS)["relacionamentos"] == 5
    assert banco.importar("PIPE", SIMBA)["transacoes"] == 32
    assert rif.parse("PIPE", RIF)["comunicacoes"] == 12
    assert tel.importar("PIPE", TELE)["eventos"] == 14 and tel.importar("PIPE", ERB)["eventos"] == 6
    assert soc.importar("PIPE", SOC)["pjs"] == 3
    assert cripto.importar("PIPE", CRIPTO)["movimentacoes"] == 10

    # 3. análises determinísticas (uma agregada salva vira fonte agg#)
    assert banco.integridade("PIPE")["total_divergencias_saldo"] == 0
    salvo = json.loads(cli("banco", "passagem", "PIPE", "--salvar").stdout)["salvo"]
    assert salvo["agg"] == "banco_passagem_horas48"
    assert banco.circularidade("PIPE")["ciclos"] == 1
    assert rif.sobreposicao("PIPE")["pares_sobrepostos"] == 1
    assert tel.ips("PIPE")["diligencias_porta"]
    assert soc.compartilhados("PIPE")["socios_compartilhados"]
    assert cripto.exchanges("PIPE")["ligadas_a_lancamento"] == 2

    # 4. achados dos especialistas (como os agentes gravariam) passam nos gates
    gravar_achados(d, "analista-rif", [
        {"id": "RIF-001", "agente": "analista-rif", "rotulo": "FATO", "relevancia": "alta", "entidades": ["PF-0001", "PJ-0001"],
         "enunciado": "Segundo o comunicante, PF-0001 recebeu R$ 150.000,00 de PJ-0001 entre 05/01/2026 e 20/02/2026.",
         "valores": [{"valor": 150000.0, "origem": "rif comunicacoes"}], "periodo": {"inicio": "2026-01-05", "fim": "2026-02-20"},
         "fontes": [{"doc_id": RIF, "localizador": "com#1"}]},
        {"id": "RIF-002", "agente": "analista-rif", "rotulo": "HIPOTESE", "relevancia": "media", "entidades": ["PF-0003", "PF-0002"],
         "enunciado": "PF-0003 pode operar como conta de passagem para PF-0002.", "fontes": [{"doc_id": RIF, "localizador": "com#8"}],
         "diligencia": {"tipo": "BAN", "alvo": "PF-0003", "motivo": "confirmar o padrão de passagem descrito pelo comunicante"}},
    ])
    gravar_achados(d, "analista-bancario", [
        {"id": "BAN-001", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "alta", "entidades": ["PF-0001", "PF-0002"],
         "enunciado": "PF-0001 transferiu R$ 20.000,00 a PF-0002 em 07/01/2026.", "valores": [{"valor": 20000.0, "origem": "banco lancamentos"}],
         "fontes": [{"doc_id": SIMBA, "localizador": "tx#2"}]},
        {"id": "BAN-002", "agente": "analista-bancario", "rotulo": "INFERENCIA", "relevancia": "alta", "entidades": ["CT-0002", "PF-0002"],
         "enunciado": "A conta CT-0002 tem perfil de passagem: R$ 62.700,00 dos R$ 63.500,00 creditados saem em até 48 h.",
         "raciocinio": "Casamento FIFO crédito→débito em 48 h com índice 0,987, acima do limiar 0,8.",
         "valores": [{"valor": 62700.0, "origem": "banco passagem --salvar"}, {"valor": 63500.0, "origem": "banco passagem --salvar"}],
         "fontes": [{"doc_id": SIMBA, "localizador": "agg#banco_passagem_horas48"}]},
    ])
    gravar_achados(d, "integrador-vinculos", [
        {"id": "INT-001", "agente": "integrador-vinculos", "rotulo": "FATO", "relevancia": "alta", "entidades": ["PF-0002", "CT-0002", "PJ-0003"],
         "enunciado": "PF-0002 depositou R$ 15.000,00 na exchange PJ-0003 a partir de CT-0002 em 11/03/2026; o débito consta do extrato.",
         "valores": [{"valor": 15000.0, "origem": "cripto exchanges"}],
         "fontes": [{"doc_id": CRIPTO, "localizador": "mov#1"}, {"doc_id": SIMBA, "localizador": "tx#17"}]},
        {"id": "INT-002", "agente": "integrador-vinculos", "rotulo": "FATO", "relevancia": "media", "entidades": ["PF-0003", "PJ-0001"],
         "enunciado": "PF-0003 deixou o quadro de PJ-0001 em 01/02/2026.", "fontes": [{"doc_id": SOC, "localizador": "qsa#2"}]},
        {"id": "INT-003", "agente": "integrador-vinculos", "rotulo": "FATO", "relevancia": "alta", "entidades": ["EML-0001"],
         "enunciado": "EML-0001 acessou de IP em faixa CGNAT sem porta lógica às 14:31:50 UTC de 10/03/2026.",
         "fontes": [{"doc_id": TELE, "localizador": "ev#5"}]},
    ])
    assert achados.validar("PIPE")["ok"]
    v = achados.verificar("PIPE")
    assert v["ok"], v
    assert achados.diligencias("PIPE")["total"] == 1

    # 5. integração
    g = grafo.construir("PIPE")
    assert g["por_tipo"]["achado"] > 0 and g["maior_componente"] >= 12      # END-0002 (só da exchange) fica isolado
    assert "PF-0002" in grafo.centrais("PIPE")["convergentes"]
    assert grafo.exportar("PIPE", formato="todos")["arquivos"]["html"]
    lt = integracao.integrada("PIPE")
    assert set(lt["por_fonte"]) == {"bancario", "rif", "ccs", "telematico", "societario", "cripto", "achado"}

    # 6. produto do redator passa na auditoria de ancoragem e no vazamento
    (d / "04_produtos" / "informacao_analise_v1.md").write_text(PRODUTO, encoding="utf-8")
    anc = achados.verificar("PIPE", arquivo="04_produtos/informacao_analise_v1.md")
    assert anc["ok"], anc
    assert cofre.vazamento("PIPE", arquivo="04_produtos/informacao_analise_v1.md")["total"] == 0

    # 7. saídas
    m = matrizes.matrizes("PIPE")
    assert m["omitidas"] == [] and m["planilhas"]["Achados"] == 7
    rd = render.render("PIPE", "informacao_analise_v1.md")
    assert rd["ok"] and rd["tokens_remanescentes"] == 0
    texto = "\n".join(p.text for p in docx.Document(rd["docx"]).paragraphs)
    assert not RE_TOKEN.search(texto) and PF1["nome"] in texto and PJ3["nome"] in texto and "[DOC-005, lançamento 2]" in texto
    assert render.render("PIPE", "matrizes.xlsx")["ok"] and render.render("PIPE", "grafo.html")["ok"]
    h = handoff.handoff("PIPE", reidentificar=True)
    assert h["resumo"]["achados"] == 7 and h["resumo"]["diligencias"] == 1 and h["reidentificado"]["tokens_remanescentes"] == 0
    pacote_h = json.loads((d / "04_produtos" / "handoff.json").read_text(encoding="utf-8"))
    assert pacote_h["produto"]["versao"] == 1 and len(pacote_h["documentos"]) == 7
    assert not any(n in json.dumps(pacote_h) for n in (PF1["nome"], PF2["cpf"], PF3["cpf"], PJ1["cnpj"]))

    # 8. estado, status e CLI de status
    caso.estado("PIPE", sets=["fase=concluido"], adds=["agentes_concluidos=analista-rif", "agentes_concluidos=analista-bancario", "agentes_concluidos=integrador-vinculos"])
    st = json.loads(cli("caso", "status", "PIPE").stdout)
    assert st["fase"] == "concluido" and st["documentos"] == 7 and len(st["agentes_concluidos"]) == 3
    assert {"informacao_analise_v1.md", "matrizes.xlsx", "grafo.html", "grafo.json", "grafo.graphml", "handoff.json"} <= set(st["produtos"])

    # 9. arquivamento cifrado, descarte e restauração íntegra
    monkeypatch.setenv(arquivo.VAR_SENHA, SENHA)
    a = arquivo.arquivar("PIPE", apagar=True)
    assert a["verificado"] and a["diretorio_apagado"] and not d.exists()
    rest = arquivo.desarquivar(a["pacote"], str(tmp_path / "rest"))
    assert rest["conferido_com_sidecar"]
    restaurado = tmp_path / "rest" / "PIPE"
    assert (restaurado / "04_produtos" / "render" / "informacao_analise_v1.docx").is_file()
    assert json.loads((restaurado / "estado.json").read_text(encoding="utf-8"))["fase"] == "arquivado"
    # o caso restaurado continua consultável pelo pacote
    monkeypatch.setenv("AGENCIA_CASOS", str(tmp_path / "rest"))
    assert banco.resumo("PIPE")["contas"] and rif.resumo("PIPE")["comunicacoes"] == 12
