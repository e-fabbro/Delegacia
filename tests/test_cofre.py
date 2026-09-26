"""F1: pseudonimizador consistente por caso e `cofre vazamento`."""
import re
import sqlite3
import stat

import pytest

from agencia import caso, cofre
from tests.fixtures.gerar import PF1, PF2, PJ1, cnpj, cpf


@pytest.fixture
def cf(raiz_casos):
    caso.novo("TESTE")
    return cofre.Cofre(raiz_casos / "TESTE" / "_cofre" / "identidades.db")


def test_mesmo_valor_mesmo_pseudonimo(cf):
    a = cf.pseudonimo("cpf", "123.456.789-09")
    b = cf.pseudonimo("cpf", "12345678909")
    assert a == b == "PF-0001"
    assert cf.pseudonimo("cpf", cpf("987654321")) == "PF-0002"
    assert cf.pseudonimo("cnpj", PJ1["cnpj"]) == "PJ-0001"
    assert cf.pseudonimo("telefone", "(61) 99876-5432") == cf.pseudonimo("telefone", "+55 61 99876 5432") == "TEL-0001"
    assert cf.pseudonimo("email", "Maria.Dores@Example.com") == cf.pseudonimo("email", "maria.dores@example.com") == "EML-0001"
    assert cf.pseudonimo("conta", "1234/56789-0") == cf.pseudonimo("conta", "1234 / 567890") == "CT-0001"


def test_persistencia_entre_instancias(raiz_casos, cf):
    cf.pseudonimo("cpf", PF1["cpf"])
    outro = cofre.Cofre(raiz_casos / "TESTE" / "_cofre" / "identidades.db")
    assert outro.pseudonimo("cpf", PF1["cpf"]) == "PF-0001"
    assert outro.pseudonimo("cpf", PF2["cpf"]) == "PF-0002"


def test_vincular_nome_ao_cpf(cf):
    p = cf.pseudonimo("cpf", PF1["cpf"])
    cf.vincular("nome_pf", PF1["nome"], p)
    assert cf.pseudonimo("nome_pf", "Maria das Dores  Silva") == p


def test_anonimizar_texto(cf):
    texto = (
        f"Titular: {PF1['nome']} CPF {PF1['cpf']}, CNPJ {PJ1['cnpj']}, tel {PF1['tel']}, "
        f"e-mail {PF1['email']}, agencia 1234 c/c 56789-0. Valor R$ 150.000,00 em 05/01/2026 às 14:32. "
        f"IP 200.100.50.25 porta 41234. Banco 001. CPF sem formato {PF1['cpf'].replace('.', '').replace('-', '')}."
    )
    cf.vincular("nome_pf", PF1["nome"], cf.pseudonimo("cpf", PF1["cpf"]))
    saida, contagens = cf.anonimizar(texto)
    assert PF1["cpf"] not in saida and "12345678909" not in saida
    assert PJ1["cnpj"] not in saida
    assert "99876" not in saida
    assert "example.com" not in saida
    assert "56789-0" not in saida
    assert "MARIA" not in saida
    assert saida.count("PF-0001") == 3  # nome + cpf formatado + cpf sem formato
    assert "PJ-0001" in saida and "TEL-0001" in saida and "EML-0001" in saida and "CT-0001" in saida
    # mantidos em claro
    for claro in ("R$ 150.000,00", "05/01/2026", "14:32", "200.100.50.25", "41234", "Banco 001"):
        assert claro in saida
    assert contagens["cpf"] == 2 and contagens["cnpj"] == 1


def test_anonimizar_nao_confunde_numeros(cf):
    texto = "Documento 000123 valor 9900,00 saldo 20700,00 em 12/03/2026 CEP 72000-000 processo 0801234-56.2026.8.07.0001"
    saida, contagens = cf.anonimizar(texto)
    assert saida == texto
    assert sum(contagens.values()) == 0


def test_anonimizar_digitos_invalidos_nao_vira_cpf(cf):
    saida, _ = cf.anonimizar("numero 111.111.111-11 e 123.456.789-00")
    assert "111.111.111-11" in saida  # dígitos repetidos: inválido
    assert "123.456.789-00" in saida  # dígito verificador errado


def test_reidentificar(cf):
    p = cf.pseudonimo("cpf", PF1["cpf"])
    cf.vincular("nome_pf", PF1["nome"], p)
    pj = cf.pseudonimo("cnpj", PJ1["cnpj"])
    t = cf.pseudonimo("telefone", PF1["tel"])
    texto = cf.reidentificar(f"{p} recebeu de {pj}; contato {t}.")
    assert PF1["nome"] in texto and PF1["cpf"] in texto
    assert PJ1["cnpj"] in texto
    assert PF1["tel"] in texto
    assert not re.search(r"\b(PF|PJ|TEL)-\d{4}\b", texto)


def test_cofre_permissoes_e_sem_valor_real_no_caso_db(raiz_casos, cf):
    cf.pseudonimo("cpf", PF1["cpf"])
    arq = raiz_casos / "TESTE" / "_cofre" / "identidades.db"
    assert stat.S_IMODE(arq.stat().st_mode) == 0o600
    con = sqlite3.connect(raiz_casos / "TESTE" / "caso.db")
    tabelas = {r[0] for r in con.execute("select name from sqlite_master")}
    assert "identidades" not in tabelas


def test_entidades(cf):
    cf.pseudonimo("cpf", PF1["cpf"])
    cf.pseudonimo("cnpj", PJ1["cnpj"])
    assert [(e["pseudonimo"], e["tipo"]) for e in cf.entidades()] == [("PF-0001", "PF"), ("PJ-0001", "PJ")]


# ---------- vazamento ----------

def test_vazamento_zero_apos_anonimizar(raiz_casos, cf):
    d = raiz_casos / "TESTE" / "02_extraido"
    texto, _ = cf.anonimizar(f"{PF1['nome']} {PF1['cpf']} {PJ1['cnpj']} {PF1['email']} {PF1['tel']}")
    (d / "DOC-001.md").write_text(texto, encoding="utf-8")
    r = cofre.vazamento("TESTE")
    assert r == {"total": 0, "por_arquivo": {}}


def test_vazamento_conta_e_nao_revela(raiz_casos, cf):
    d = raiz_casos / "TESTE" / "02_extraido"
    cf.vincular("nome_pf", PF1["nome"], cf.pseudonimo("cpf", PF1["cpf"]))
    (d / "DOC-001.md").write_text(f"texto com {PF1['cpf']} e {PJ1['cnpj']}", encoding="utf-8")
    (d / "DOC-002.md").write_text(f"nome em claro: {PF1['nome'].title()} e {PF1['email']}", encoding="utf-8")
    (d / "DOC-003.md").write_text("limpo PF-0001", encoding="utf-8")
    r = cofre.vazamento("TESTE")
    assert r["total"] == 4
    assert r["por_arquivo"] == {"DOC-001.md": 2, "DOC-002.md": 2}
    assert PF1["cpf"] not in str(r) and "MARIA" not in str(r).upper()


def test_vazamento_arquivo_especifico(raiz_casos, cf):
    prod = raiz_casos / "TESTE" / "04_produtos" / "informacao_analise_v1.md"
    prod.write_text(f"conclusao cita {cnpj('999888777000')}", encoding="utf-8")
    r = cofre.vazamento("TESTE", arquivo="04_produtos/informacao_analise_v1.md")
    assert r["total"] == 1
    assert list(r["por_arquivo"]) == ["04_produtos/informacao_analise_v1.md"]
