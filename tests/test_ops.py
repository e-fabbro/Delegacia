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
