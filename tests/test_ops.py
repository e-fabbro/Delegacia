"""F0: scripts de sistema são gerados (não executados) e passam na checagem de sintaxe."""
import subprocess

import pytest

SCRIPTS = ["ops/setup_vps.sh", "ops/nexo_egress_refresh.sh", "ops/nexo_montar_casos.sh"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_sintaxe_bash(raiz_projeto, script):
    p = subprocess.run(["bash", "-n", str(raiz_projeto / script)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_modo_estrito(raiz_projeto, script):
    texto = (raiz_projeto / script).read_text(encoding="utf-8")
    assert texto.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in texto


def test_setup_vps_cobre_requisitos_f0(raiz_projeto):
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert "useradd" in texto and "nexo" in texto
    assert "gocryptfs" in texto and "/srv/casos" in texto
    assert "nft" in texto and "skuid" in texto
    assert "api.anthropic.com" in texto and "api.telegram.org" in texto
    assert "/var/log/agencia-nexo" in texto
    # só gera: nenhum comando é executado durante os testes
    assert "exit 1" in texto  # aborta se não for root


def test_refresh_resolve_hosts_e_alimenta_sets(raiz_projeto):
    texto = (raiz_projeto / "ops" / "nexo_egress_refresh.sh").read_text(encoding="utf-8")
    assert "getent" in texto
    assert "nft add element" in texto


def _hosts(texto):
    import re
    m = re.search(r'HOSTS_PERMITIDOS="(?:\$\{HOSTS_PERMITIDOS:-)?([^}"]+)\}?"', texto)
    assert m, "HOSTS_PERMITIDOS não encontrado"
    return set(m.group(1).split())


def test_setup_nao_toca_ruleset_global(raiz_projeto):
    # /etc/nftables.conf do Ubuntu começa com `flush ruleset`: habilitar nftables.service
    # ou incluir algo nele apaga as tabelas do Docker que já rodam na VPS.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert "flush ruleset" not in texto
    assert "/etc/nftables.conf" not in texto
    assert "enable --now nftables" not in texto
    assert "nexo-egress-nft.service" in texto  # unidade própria, só a tabela inet nexo_egress


def test_so_oauth_sem_chave_de_api(raiz_projeto):
    for pasta in ("ops", "hermes"):
        for arq in (raiz_projeto / pasta).iterdir():
            assert "ANTHROPIC_API_KEY" not in arq.read_text(encoding="utf-8"), arq.name


def test_hosts_permitidos_iguais_e_cobrem_oauth(raiz_projeto):
    setup = _hosts((raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8"))
    refresh = _hosts((raiz_projeto / "ops" / "nexo_egress_refresh.sh").read_text(encoding="utf-8"))
    assert setup == refresh
    assert {"api.anthropic.com", "api.telegram.org", "console.anthropic.com", "platform.claude.com"} <= setup


def test_venv_usa_python_do_sistema(raiz_projeto):
    # venv criado por root com Python gerenciado pelo uv apontaria para /root, ilegível pelo nexo.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert "UV_PYTHON_PREFERENCE=only-system" in texto
    assert "uv sync --frozen" in texto
    assert "/usr/local/bin" in texto  # uv visível para root e nexo
