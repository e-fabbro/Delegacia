"""F4: `achados validar/verificar/diligencias` — schema, ponteiros, valores, entidades, ancoragem de nota/produto."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agencia import achados, banco, caso, ingestao, rif

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CCS, RIF, SIMBA = "DOC-001", "DOC-002", "DOC-003"


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True)


def gravar(d: Path, agente: str, achados_: list[dict], nota: str | None = None) -> Path:
    pasta = d / "03_analises" / agente
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "achados.jsonl").write_text("\n".join(json.dumps(a, ensure_ascii=False) for a in achados_) + "\n", encoding="utf-8")
    if nota is not None:
        (pasta / "nota.md").write_text(nota, encoding="utf-8")
    return pasta


ACHADOS_RIF = [
    {"id": "RIF-001", "agente": "analista-rif", "rotulo": "FATO", "relevancia": "alta",
     "enunciado": "Segundo o comunicante, PF-0001 recebeu R$ 150.000,00 de PJ-0001 entre 05/01/2026 e 20/02/2026.",
     "entidades": ["PF-0001", "PJ-0001"], "valores": [{"valor": 150000.00, "origem": "rif comunicacoes TESTE --envolvido PF-0001"}],
     "periodo": {"inicio": "2026-01-05", "fim": "2026-02-20"}, "fontes": [{"doc_id": RIF, "localizador": "com#1"}]},
    {"id": "RIF-002", "agente": "analista-rif", "rotulo": "INFERENCIA", "relevancia": "media",
     "enunciado": "As comunicações 1 e 2 descrevem em parte a mesma movimentação de PF-0001; o piso consolidado é R$ 168.000,00.",
     "raciocinio": "Períodos sobrepostos entre 01/02 e 20/02/2026 em comunicantes distintos; adota-se o maior valor por grupo.",
     "entidades": ["PF-0001"], "valores": [{"valor": 168000.00, "origem": "rif sobreposicao TESTE"}],
     "fontes": [{"doc_id": RIF, "localizador": "com#1"}, {"doc_id": RIF, "localizador": "com#2"}, {"doc_id": RIF, "localizador": "agg#rif_sobreposicao"}]},
    {"id": "RIF-003", "agente": "analista-rif", "rotulo": "HIPOTESE", "relevancia": "media",
     "enunciado": "PF-0003 pode operar como conta de passagem para PF-0002.",
     "entidades": ["PF-0003", "PF-0002"], "fontes": [{"doc_id": RIF, "localizador": "com#8"}, {"doc_id": RIF, "localizador": "p3"}],
     "diligencia": {"tipo": "BAN", "alvo": "PF-0003", "motivo": "confirmar o padrão de passagem descrito pelo comunicante"}},
]

ACHADOS_BAN = [
    {"id": "BAN-001", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "alta",
     "enunciado": "PF-0001 transferiu R$ 20.000,00 a PF-0002 em 07/01/2026 via PIX.",
     "entidades": ["PF-0001", "PF-0002"], "valores": [{"valor": 20000.00, "origem": "banco lancamentos TESTE --conta CT-0001"}],
     "periodo": {"inicio": "2026-01-07", "fim": "2026-01-07"}, "fontes": [{"doc_id": SIMBA, "localizador": "tx#2"}]},
    {"id": "BAN-002", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "alta",
     "enunciado": "A conta CT-0002 apresenta índice de passagem 0,987: R$ 62.700,00 dos R$ 63.500,00 creditados saíram em até 48 h.",
     "entidades": ["CT-0002", "PF-0002"], "valores": [{"valor": 62700.00, "origem": "banco passagem TESTE --salvar"}, {"valor": 63500.00, "origem": "banco passagem TESTE --salvar"}],
     "fontes": [{"doc_id": SIMBA, "localizador": "agg#banco_passagem_horas48"}]},
    {"id": "BAN-003", "agente": "analista-bancario", "rotulo": "LIMITACAO", "relevancia": "media",
     "enunciado": "A conta CT-0004, de PF-0002, consta do CCS sem extrato entregue.",
     "entidades": ["CT-0004", "PF-0002"], "fontes": [{"doc_id": CCS, "localizador": "ccs#4"}],
     "diligencia": {"tipo": "OFICIO_BANCO", "alvo": "CT-0004", "motivo": "complementação do afastamento"}},
]

NOTA_OK = """# Nota técnica — analista-bancario

## 1. Material
Três contas com extrato [F:DOC-003:tx#1].

| conta | lançamentos | fonte |
|---|---|---|
| CT-0001 | 12 | [F:DOC-003:agg#banco_passagem_horas48] |

- PF-0001 transferiu R$ 20.000,00 a PF-0002 em 07/01/2026 [F:DOC-003:tx#2].
Parágrafo de contexto sem número nem entidade não exige ponteiro.
"""

NOTA_RUIM = NOTA_OK + """
FATO: PF-0002 recebeu R$ 30.000,00 de PF-0004 em 10/03/2026.
Lançamento inexistente [F:DOC-003:tx#999].
Entidade PF-0099 não existe no caso [F:DOC-003:tx#1].
"""


@pytest.fixture
def caso_completo(raiz_casos):
    caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    for nome in ("ccs_3contas.xlsx", "rif_12_sintetico.pdf", "simba_3contas.csv"):
        shutil.copy(FIXTURES / nome, d / "00_brutos" / nome)
    ingestao.ingerir("TESTE")
    banco.importar("TESTE", CCS)
    banco.importar("TESTE", SIMBA)
    rif.parse("TESTE", RIF)
    assert cli("banco", "passagem", "TESTE", "--salvar").returncode == 0
    from agencia import agregados
    agregados.salvar("TESTE", "rif_sobreposicao", {}, {**rif.sobreposicao("TESTE"), "docs": [RIF]})
    gravar(d, "analista-rif", ACHADOS_RIF)
    gravar(d, "analista-bancario", ACHADOS_BAN, NOTA_OK)
    return d


def test_validar_ok(caso_completo):
    r = achados.validar("TESTE")
    assert r["ok"] and r["achados"] == 6 and r["erros"] == 0
    assert [a["agente"] for a in r["por_agente"]] == ["analista-bancario", "analista-rif"]
    assert achados.validar("TESTE", "analista-rif")["achados"] == 3


def test_validar_erros_de_schema(caso_completo):
    ruins = [
        {"id": "x1", "agente": "analista-rif", "rotulo": "FATO", "enunciado": "curto", "fontes": [], "relevancia": "alta"},
        {"id": "RIF-010", "agente": "analista-rif", "rotulo": "INFERENCIA", "enunciado": "Inferência sem raciocínio explícito.", "relevancia": "alta",
         "fontes": [{"doc_id": RIF, "localizador": "com#1"}]},
        {"id": "RIF-011", "agente": "analista-rif", "rotulo": "HIPOTESE", "enunciado": "Hipótese sem diligência associada.", "relevancia": "baixa",
         "fontes": [{"doc_id": RIF, "localizador": "com#1"}]},
        {"id": "RIF-011", "agente": "analista-bancario", "rotulo": "FATO", "enunciado": "Id repetido e agente errado.", "relevancia": "baixa",
         "fontes": [{"doc_id": "DOC-099", "localizador": "p1"}], "entidades": ["PF-1"]},
    ]
    gravar(caso_completo, "analista-rif", ruins)
    (caso_completo / "03_analises" / "analista-rif" / "achados.jsonl").open("a", encoding="utf-8").write("{isto nao e json\n")
    r = achados.validar("TESTE", "analista-rif")
    assert not r["ok"]
    erros = " | ".join(e["erro"] for e in r["por_agente"][0]["lista"])
    for esperado in ("id: 'x1' does not match", "too short", "non-empty", "raciocinio", "diligencia", "repetido", "difere da pasta",
                     "não consta do manifesto", "entidades/0", "JSON inválido"):
        assert esperado in erros, esperado


def test_verificar_ok(caso_completo):
    r = achados.verificar("TESTE")
    assert r["erros"] == 0, r
    assert r["ok"] and r["achados"] == 6
    ban = [a for a in r["por_agente"] if a["agente"] == "analista-bancario"][0]
    assert ban["avisos"] == 0


def test_criterio_aceite_6_valor_adulterado(caso_completo):
    adulterado = json.loads(json.dumps(ACHADOS_BAN))
    adulterado[0]["valores"][0]["valor"] = 25000.00          # lançamento tx#2 é de 20.000,00
    adulterado[1]["valores"][0]["valor"] = 62701.00          # agregado diz 62.700,00
    gravar(caso_completo, "analista-bancario", adulterado)
    r = achados.verificar("TESTE", "analista-bancario")
    assert not r["ok"] and r["erros"] == 2
    erros = r["por_agente"][0]["lista_erros"]
    assert erros[0]["id"] == "BAN-001" and "25000.0" in erros[0]["erro"] and "não consta" in erros[0]["erro"]
    assert erros[1]["id"] == "BAN-002" and "62701.0" in erros[1]["erro"]


def test_verificar_ponteiros_entidades_e_avisos(caso_completo):
    ruins = [
        {"id": "BAN-010", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "alta", "enunciado": "Ponteiro para lançamento inexistente.",
         "fontes": [{"doc_id": SIMBA, "localizador": "tx#999"}]},
        {"id": "BAN-011", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "alta", "enunciado": "Página inexistente do RIF e agregado não salvo.",
         "fontes": [{"doc_id": RIF, "localizador": "p9"}, {"doc_id": SIMBA, "localizador": "agg#banco_resumo"}, {"doc_id": RIF, "localizador": "ev#1"}]},
        {"id": "BAN-012", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "alta", "enunciado": "Entidade PF-0099 inexistente e PF-0004 fora das fontes.",
         "entidades": ["PF-0099", "PF-0004"], "fontes": [{"doc_id": SIMBA, "localizador": "tx#2"}], "valores": [{"valor": 20000.0, "origem": "x"}]},
        {"id": "BAN-013", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "alta", "enunciado": "Agregado de outro documento.",
         "fontes": [{"doc_id": RIF, "localizador": "agg#banco_passagem_horas48"}]},
        {"id": "BAN-014", "agente": "analista-bancario", "rotulo": "FATO", "relevancia": "baixa", "enunciado": "Período que não bate com a fonte.",
         "periodo": {"inicio": "2025-01-01", "fim": "2025-12-31"}, "fontes": [{"doc_id": SIMBA, "localizador": "tx#2"}]},
    ]
    gravar(caso_completo, "analista-bancario", ruins)
    r = achados.verificar("TESTE", "analista-bancario")
    e = r["por_agente"][0]
    erros = " | ".join(x["erro"] for x in e["lista_erros"])
    assert "tx#999" in erros and "p9" in erros and "agg#banco_resumo" in erros and "ev#1" in erros and "não deriva de" in erros
    assert "PF-0099 não existe" in erros
    avisos = " | ".join(x["aviso"] for x in e["lista_avisos"])
    assert "PF-0004" in avisos and "período declarado" in avisos          # PF-0004 declarada mas ausente do lançamento citado
    assert r["erros"] >= 6


def test_criterio_aceite_5_frase_sem_ponteiro(caso_completo):
    r = achados.verificar("TESTE", arquivo="03_analises/analista-bancario/nota.md")
    assert r["ok"] and r["erros"] == 0 and r["ponteiros_ok"] == 3
    (caso_completo / "04_produtos" / "informacao_analise_v1.md").write_text(NOTA_RUIM, encoding="utf-8")
    r = achados.verificar("TESTE", arquivo="04_produtos/informacao_analise_v1.md")
    assert not r["ok"]
    assert len(r["frases_sem_ponteiro"]) == 1 and "R$ 30.000,00" in r["frases_sem_ponteiro"][0]["texto"]
    assert len(r["ponteiros_que_nao_resolvem"]) == 1 and r["ponteiros_que_nao_resolvem"][0]["ponteiro"] == "[F:DOC-003:tx#999]"
    assert [e["entidade"] for e in r["entidades_desconhecidas"]] == ["PF-0099"]
    with pytest.raises(caso.ErroCaso):
        achados.verificar("TESTE", arquivo="../fora.md")


def test_diligencias(caso_completo):
    r = achados.diligencias("TESTE")
    assert r["total"] == 2
    tipos = {g["tipo"]: g["diligencias"] for g in r["por_tipo"]}
    assert set(tipos) == {"BAN", "OFICIO_BANCO"}
    assert tipos["BAN"][0]["alvo"] == "PF-0003" and tipos["BAN"][0]["achados"] == ["analista-rif:RIF-003"]
    # duplicata (mesmo tipo/alvo em outro agente) é consolidada
    gravar(caso_completo, "integrador-vinculos", [{**ACHADOS_RIF[2], "id": "INT-001", "agente": "integrador-vinculos", "relevancia": "alta",
                                                  "diligencia": {"tipo": "BAN", "alvo": "PF-0003", "motivo": "outro motivo"}}])
    r = achados.diligencias("TESTE")
    ban = {g["tipo"]: g["diligencias"] for g in r["por_tipo"]}["BAN"][0]
    assert r["total"] == 2 and len(ban["motivos"]) == 2 and ban["relevancia"] == "alta" and set(ban["agentes"]) == {"analista-rif", "integrador-vinculos"}


def test_cli_achados(caso_completo):
    r = cli("achados", "validar", "TESTE", "--md")
    assert r.returncode == 0 and "| agente |" in r.stdout
    r = cli("achados", "verificar", "TESTE", "analista-rif")
    assert r.returncode == 0 and json.loads(r.stdout)["ok"] is True
    r = cli("achados", "verificar", "TESTE", "--arquivo", "03_analises/analista-bancario/nota.md")
    assert r.returncode == 0 and json.loads(r.stdout)["ok"] is True
    r = cli("achados", "diligencias", "TESTE", "--md")
    assert r.returncode == 0 and "OFICIO_BANCO" in r.stdout
    r = cli("achados", "validar", "TESTE", "inexistente")
    assert r.returncode == 1 and "erro" in r.stderr
