"""Runtime Codex: AGENTS.md orquestra; .codex/agents/*.toml define os 9 subagentes."""
import re
import tomllib

import pytest

AGENTES = [
    "triagem-custodia", "analista-rif", "analista-bancario", "analista-telematico",
    "analista-societario", "analista-cripto", "integrador-vinculos", "redator", "revisor-prova",
]
RESIDUOS_CLAUDE = re.compile(r"claude -p|chamadas Task|\.claude/|CLAUDE_PROJECT_DIR")


def _agente(raiz, nome):
    return tomllib.loads((raiz / ".codex" / "agents" / f"{nome}.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("nome", AGENTES)
def test_agente_codex_valido(raiz_projeto, nome):
    a = _agente(raiz_projeto, nome)
    assert a["name"] == nome
    assert len(a["description"]) > 40
    assert len(a["developer_instructions"]) > 200
    assert a.get("model_reasoning_effort") in ("low", "medium", "high")
    assert not RESIDUOS_CLAUDE.search(a["developer_instructions"])


def test_so_os_nove_agentes(raiz_projeto):
    nomes = sorted(p.stem for p in (raiz_projeto / ".codex" / "agents").glob("*.toml"))
    assert nomes == sorted(AGENTES)


def test_revisor_e_triagem_nao_redigem(raiz_projeto):
    assert "não escreve arquivos" in _agente(raiz_projeto, "revisor-prova")["developer_instructions"]
    assert "não escreve arquivos" in _agente(raiz_projeto, "triagem-custodia")["developer_instructions"]


def test_agents_md_orquestra(raiz_projeto):
    texto = (raiz_projeto / "AGENTS.md").read_text(encoding="utf-8")
    assert "Regras invioláveis" in texto and "Nunca leia `00_brutos/` nem `_cofre/`" in texto
    assert "CASO <COD> · fase <X> concluída" in texto
    assert "codex exec" in texto
    assert not RESIDUOS_CLAUDE.search(texto)
    for nome in AGENTES:
        assert f"`{nome}`" in texto, nome


def test_sem_runtime_claude(raiz_projeto):
    assert not (raiz_projeto / ".claude").exists()
    # CLAUDE.md fica só como ponteiro para sessões de construção
    ponteiro = (raiz_projeto / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in ponteiro and len(ponteiro.splitlines()) < 15


# ---------- modo econômico (cota ChatGPT compartilhada com os gateways do Hermes) ----------

ESFORCO_ECONOMICO = {
    "triagem-custodia": "low", "analista-rif": "low", "analista-bancario": "low", "analista-telematico": "low",
    "analista-societario": "low", "analista-cripto": "low",
    "integrador-vinculos": "medium", "redator": "medium",
    "revisor-prova": "high",  # o gate de prova não economiza
}


@pytest.mark.parametrize("nome,esforco", ESFORCO_ECONOMICO.items())
def test_esforco_economico(raiz_projeto, nome, esforco):
    assert _agente(raiz_projeto, nome)["model_reasoning_effort"] == esforco


def test_agents_md_modo_economico(raiz_projeto):
    texto = (raiz_projeto / "AGENTS.md").read_text(encoding="utf-8")
    for trecho in (
        "## Economia",
        "No máximo 3 subagentes abertos ao mesmo tempo",
        "Feche cada subagente",
        "timeout_ms",
        "Não leia `docs/PROJETO.md`",
        "Revisão em lote",
        "você mesmo roda a ingestão",
    ):
        assert trecho in texto, trecho
    # o gate continua: toda entrega passa pelo revisor
    assert "toda entrega de especialista, do integrador e do redator passa pelo `revisor-prova`" in texto


def test_revisor_aceita_lote(raiz_projeto):
    instr = _agente(raiz_projeto, "revisor-prova")["developer_instructions"]
    assert "Revisão em lote" in instr and "um parecer por agente" in instr
