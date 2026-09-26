"""F1: `ingerir` — hash, manifesto, cadeia de custódia, classificação, extração, pseudonimização."""
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agencia import caso, ingestao
from tests.fixtures.gerar import PF1, PF2, PJ1, PJ2

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# ingestão numera por ordem alfabética de nome
BRUTOS = ["anotacoes.txt", "ccs_sintetico.xlsx", "imagem.bin", "rif_sintetico.pdf", "simba_sintetico.csv"]
TXT, CCS, BIN, RIF, SIMBA = "DOC-001", "DOC-002", "DOC-003", "DOC-004", "DOC-005"
RE_CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
RE_CNPJ = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")


@pytest.fixture
def caso_com_brutos(raiz_casos):
    caso.novo("TESTE")
    brutos = raiz_casos / "TESTE" / "00_brutos"
    for nome in BRUTOS:
        shutil.copy(FIXTURES / nome, brutos / nome)
    return raiz_casos / "TESTE"


def manifesto(d):
    return json.loads((d / "01_custodia" / "manifesto.json").read_text(encoding="utf-8"))


def test_ingerir_manifesto(caso_com_brutos):
    d = caso_com_brutos
    r = ingestao.ingerir("TESTE")
    assert [e["doc_id"] for e in r["novos"]] == ["DOC-001", "DOC-002", "DOC-003", "DOC-004", "DOC-005"]
    m = manifesto(d)
    assert len(m) == 5
    por_nome = {e["nome"]: e for e in m}
    assert set(BRUTOS) == set(por_nome)
    rif = por_nome["rif_sintetico.pdf"]
    assert rif["sha256"] == hashlib.sha256((FIXTURES / "rif_sintetico.pdf").read_bytes()).hexdigest()
    assert rif["tamanho"] == (FIXTURES / "rif_sintetico.pdf").stat().st_size
    assert rif["recebido_em"] and rif["ingerido_em"]
    for chave in ("doc_id", "nome", "sha256", "tamanho", "recebido_em", "tipo", "confianca_classificacao", "extracao", "avisos"):
        assert chave in rif
    assert r["por_tipo"]["RIF"] == 1
    assert (d / "00_brutos" / "rif_sintetico.pdf").read_bytes() == (FIXTURES / "rif_sintetico.pdf").read_bytes()


def test_classificacao(caso_com_brutos):
    ingestao.ingerir("TESTE")
    por_nome = {e["nome"]: e for e in manifesto(caso_com_brutos)}
    assert por_nome["rif_sintetico.pdf"]["tipo"] == "RIF"
    assert por_nome["simba_sintetico.csv"]["tipo"] == "SIMBA"
    assert por_nome["ccs_sintetico.xlsx"]["tipo"] == "CCS"
    assert por_nome["anotacoes.txt"]["tipo"] == "OUTRO"
    assert por_nome["imagem.bin"]["tipo"] == "OUTRO"
    assert por_nome["rif_sintetico.pdf"]["confianca_classificacao"] >= 0.8
    assert any("formato" in a for a in por_nome["imagem.bin"]["avisos"])


def test_cadeia_de_custodia(caso_com_brutos):
    ingestao.ingerir("TESTE")
    linhas = (caso_com_brutos / "01_custodia" / "cadeia.jsonl").read_text(encoding="utf-8").splitlines()
    eventos = [json.loads(l) for l in linhas]
    etapas_doc1 = [e["etapa"] for e in eventos if e["doc_id"] == "DOC-001"]
    assert etapas_doc1 == ["recebimento", "fixacao", "processamento", "armazenamento"]
    for e in eventos:
        assert e["etapa"] in ingestao.custodia.ETAPAS_158B
        for chave in ("ts", "doc_id", "etapa", "responsavel", "descricao", "sha256"):
            assert chave in e
    assert eventos[0]["ts"].endswith("+00:00")


def test_extracao_pdf_com_paginas_e_pseudonimizada(caso_com_brutos):
    ingestao.ingerir("TESTE")
    md = (caso_com_brutos / "02_extraido" / f"{RIF}.md").read_text(encoding="utf-8")
    assert md.startswith(f"# {RIF}")
    assert "<!-- p1 -->" in md and "<!-- p2 -->" in md
    assert "COMUNICACAO 1" in md and "R$ 150.000,00" in md and "05/01/2026" in md
    for real in (PF1["cpf"], PF2["cpf"], PJ1["cnpj"], PJ2["cnpj"], PF1["email"], "99876", "MARIA", "PEREIRA", "ALFA COMERCIO"):
        assert real not in md, real
    assert "PF-0001" in md and "PJ-0001" in md and "TEL-0001" in md and "EML-0001" in md and "CT-0001" in md


def test_criterio_aceite_3_grep_cpf_cnpj_zero(caso_com_brutos):
    ingestao.ingerir("TESTE")
    extraido = caso_com_brutos / "02_extraido"
    for arq in extraido.rglob("*"):
        if arq.is_file():
            texto = arq.read_text(encoding="utf-8")
            assert not RE_CPF.search(texto), arq
            assert not RE_CNPJ.search(texto), arq
    p = subprocess.run(["grep", "-rE", r"[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2}", str(extraido)], capture_output=True)
    assert p.returncode == 1  # grep: nenhuma ocorrência


def test_tabelas_csv_pseudonimizadas_e_consistentes(caso_com_brutos):
    ingestao.ingerir("TESTE")
    d = caso_com_brutos / "02_extraido"
    tab = list((d / f"{SIMBA}.tabelas").glob("*.csv"))
    assert len(tab) == 1
    conteudo = tab[0].read_text(encoding="utf-8")
    linhas = conteudo.splitlines()
    assert linhas[0].startswith("NUMERO_BANCO")
    assert len(linhas) == 5
    assert PJ1["cnpj"] not in conteudo and "ALFA" not in conteudo and PF2["cpf"] not in conteudo
    # valores, datas e bancos em claro
    assert "150000,00" in conteudo and "05/01/2026" in conteudo and "237" in conteudo and "TAGUATINGA DF" in conteudo
    # mesma entidade no RIF e no SIMBA recebe o mesmo pseudônimo
    md_rif = (d / f"{RIF}.md").read_text(encoding="utf-8")
    pj_alfa = re.search(r"Remetente principal: (PJ-\d{4})", md_rif).group(1)
    assert pj_alfa in linhas[1]
    assert linhas[1].count(pj_alfa) == 3  # CNPJ, nome e menção no histórico da mesma PJ
    # conta do titular (ag 1234 / 56789-0) é a mesma CT do RIF
    ct = re.search(r"(CT-\d{4})", md_rif).group(1)
    assert ct in linhas[1]
    # xlsx: uma tabela por planilha
    assert [p.name for p in (d / f"{CCS}.tabelas").glob("*.csv")] == ["Relacionamentos.csv"]


def test_txt_com_linhas(caso_com_brutos):
    ingestao.ingerir("TESTE")
    md = (caso_com_brutos / "02_extraido" / f"{TXT}.md").read_text(encoding="utf-8")
    assert "R$ 1.234,56" in md and "10/04/2026" in md
    assert "99876" not in md and "MARIA" not in md and "jcp@" not in md
    por_nome = {e["nome"]: e for e in manifesto(caso_com_brutos)}
    assert por_nome["anotacoes.txt"]["extracao"]["linhas"] == 4


def test_texto_com_instrucao_gera_alerta(caso_com_brutos):
    ingestao.ingerir("TESTE")
    alertas = caso.estado("TESTE")["alertas"]
    assert any(RIF in a and "instru" in a.lower() for a in alertas)


def test_caso_db_documentos_e_entidades(caso_com_brutos):
    ingestao.ingerir("TESTE")
    con = sqlite3.connect(caso_com_brutos / "caso.db")
    docs = con.execute("select doc_id, nome, tipo from documentos order by doc_id").fetchall()
    assert len(docs) == 5 and docs[0] == (TXT, "anotacoes.txt", "OUTRO") and docs[3] == (RIF, "rif_sintetico.pdf", "RIF")
    ents = con.execute("select pseudonimo, tipo from entidades order by pseudonimo").fetchall()
    assert ("PF-0001", "PF") in ents and ("PJ-0001", "PJ") in ents
    colunas = [c[1] for c in con.execute("pragma table_info(entidades)")]
    assert "valor" not in colunas and "nome" not in colunas


def test_ingerir_idempotente(caso_com_brutos):
    r1 = ingestao.ingerir("TESTE")
    r2 = ingestao.ingerir("TESTE")
    assert r2["novos"] == [] and len(r2["ignorados"]) == 5
    assert manifesto(caso_com_brutos) == manifesto(caso_com_brutos)
    assert len(manifesto(caso_com_brutos)) == 5
    assert caso.status("TESTE")["brutos"] == {"arquivos": 5, "ingeridos": 5, "pendentes": 0}
    assert caso.status("TESTE")["fase"] == "ingestao"


def test_bruto_alterado_gera_aviso_e_alerta(caso_com_brutos):
    ingestao.ingerir("TESTE")
    (caso_com_brutos / "00_brutos" / "anotacoes.txt").write_text("alterado", encoding="utf-8")
    r = ingestao.ingerir("TESTE")
    assert r["novos"] == []
    assert any("anotacoes.txt" in a and "hash" in a for a in r["avisos"])
    assert any("anotacoes.txt" in a for a in caso.estado("TESTE")["alertas"])
    assert len(manifesto(caso_com_brutos)) == 5


def test_novo_bruto_depois(caso_com_brutos):
    ingestao.ingerir("TESTE")
    (caso_com_brutos / "00_brutos" / "z_novo.txt").write_text(f"contato {PF1['tel']}", encoding="utf-8")
    r = ingestao.ingerir("TESTE")
    assert [e["doc_id"] for e in r["novos"]] == ["DOC-006"]
    assert "TEL-0001" in (caso_com_brutos / "02_extraido" / "DOC-006.md").read_text(encoding="utf-8")


def test_reclassificar(caso_com_brutos):
    ingestao.ingerir("TESTE")
    r = ingestao.ingerir("TESTE", reclassificar=TXT, tipo="TELEMATICA")
    assert r["reclassificado"] == {"doc_id": TXT, "tipo": "TELEMATICA"} and r["anterior"] == "OUTRO"
    por_id = {e["doc_id"]: e for e in manifesto(caso_com_brutos)}
    assert por_id[TXT]["tipo"] == "TELEMATICA" and por_id[TXT]["confianca_classificacao"] == 1.0
    eventos = [json.loads(l) for l in (caso_com_brutos / "01_custodia" / "cadeia.jsonl").read_text(encoding="utf-8").splitlines()]
    assert eventos[-1]["doc_id"] == TXT and "reclassifica" in eventos[-1]["descricao"]
    con = sqlite3.connect(caso_com_brutos / "caso.db")
    assert con.execute("select tipo from documentos where doc_id=?", (TXT,)).fetchone()[0] == "TELEMATICA"
    with pytest.raises(caso.ErroCaso):
        ingestao.ingerir("TESTE", reclassificar=TXT, tipo="INVALIDO")
    with pytest.raises(caso.ErroCaso):
        ingestao.ingerir("TESTE", reclassificar="DOC-099", tipo="RIF")


def test_status_manifesto_md(caso_com_brutos):
    ingestao.ingerir("TESTE")
    s = caso.status("TESTE", manifesto=True)
    assert s["documentos"] == 5
    assert s["manifesto"][0]["doc_id"] == "DOC-001"


# ---------- CLI ----------

def cli(*args):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True)


def test_cli_ingerir_e_vazamento(caso_com_brutos):
    p = cli("ingerir", "TESTE")
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    assert len(r["novos"]) == 5
    p = cli("cofre", "vazamento", "TESTE")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["total"] == 0
    p = cli("ingerir", "TESTE", "--reclassificar", TXT, "--tipo", "TELEMATICA")
    assert p.returncode == 0, p.stderr
    p = cli("caso", "status", "TESTE", "--manifesto", "--md")
    assert TXT in p.stdout and "TELEMATICA" in p.stdout
