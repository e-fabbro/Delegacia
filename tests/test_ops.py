"""F0: scripts de sistema são gerados (não executados) e passam na checagem de sintaxe."""
import subprocess

import pytest

SCRIPTS = ["ops/setup_vps.sh", "ops/nexo_egress_refresh.sh", "ops/nexo_montar_casos.sh",
           "ops/nexo_exec.sh", "ops/teste_headless.sh", "hermes/nexo_run.sh", "ops/instalar_ponte.sh"]


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
    assert "chatgpt.com" in texto and "api.telegram.org" in texto
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


def test_so_login_chatgpt_sem_chave_de_api(raiz_projeto):
    # Chave de API só pode aparecer sendo removida do ambiente (`env -u NOME`).
    import re
    chave = re.compile(r"(ANTHROPIC|OPENAI|CODEX)_API_KEY")
    for pasta in ("ops", "hermes"):
        for arq in (raiz_projeto / pasta).iterdir():
            if not arq.is_file() or arq.suffix == ".pyc":
                continue
            for linha in arq.read_text(encoding="utf-8").splitlines():
                restante = re.sub(r"-u (ANTHROPIC|OPENAI|CODEX)_API_KEY", "", linha)
                assert not chave.search(restante), f"{arq.name}: {linha.strip()}"


def test_hosts_permitidos_iguais_e_cobrem_login_chatgpt(raiz_projeto):
    setup = _hosts((raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8"))
    refresh = _hosts((raiz_projeto / "ops" / "nexo_egress_refresh.sh").read_text(encoding="utf-8"))
    assert setup == refresh
    assert {"chatgpt.com", "auth.openai.com", "api.openai.com", "api.telegram.org"} <= setup
    assert not any("anthropic" in h or "claude" in h for h in setup)


def test_setup_configura_codex_do_nexo(raiz_projeto):
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert ".codex/config.toml" in texto
    for trecho in ('sandbox_mode = "workspace-write"', 'approval_policy = "never"', 'web_search = "disabled"',
                   "unified_exec = false", "hooks = true", "network_access = false",
                   'writable_roots = ["${CASOS_DIR}"]', 'trust_level = "trusted"'):
        assert trecho in texto, trecho
    assert "codex login --device-auth" in texto
    assert "claude" not in texto.lower()


def test_repositorio_so_leitura_para_o_nexo(raiz_projeto):
    # workspace-write deixa o modelo gravar no diretório de trabalho: o repositório (hooks, agentes)
    # tem de ser root:root para o nexo não conseguir desligar os próprios guardas.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert 'chown -R root:root "${REPO_DIR}"' in texto
    assert 'chown -R "${NEXO_USER}:${NEXO_USER}" "${REPO_DIR}"' not in texto


def test_venv_usa_python_do_sistema(raiz_projeto):
    # venv criado por root com Python gerenciado pelo uv apontaria para /root, ilegível pelo nexo.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert "UV_PYTHON_PREFERENCE=only-system" in texto
    assert "uv sync --frozen" in texto
    assert "/usr/local/bin" in texto  # uv visível para root e nexo


def test_setup_sandbox_codex_no_ubuntu_2404(raiz_projeto):
    # bwrap do sistema + perfil AppArmor do Ubuntu; nunca desligar a restrição de userns globalmente.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert "bubblewrap" in texto and "apparmor-profiles" in texto
    assert "/usr/share/apparmor/extra-profiles/bwrap-userns-restrict" in texto
    assert "apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict" in texto
    assert "apparmor_restrict_unprivileged_userns=0" not in texto


def test_nft_permite_icmp_do_nexo(raiz_projeto):
    # descoberta de vizinhos IPv6 (ff02::1:ff..) é ICMPv6: descartá-la quebra o IPv6 do nexo.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert "meta l4proto ipv6-icmp accept" in texto
    assert "meta l4proto icmp accept" in texto


def test_setup_recarrega_regras_ao_reexecutar(raiz_projeto):
    # `enable --now` não recarrega um oneshot já ativo: regra nova só vale com restart.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert "systemctl restart nexo-egress-nft.service" in texto
    assert texto.index("systemctl restart nexo-egress-nft.service") < texto.index("systemctl start nexo-egress-refresh.service")


def test_pasta_agents_existe_para_o_sandbox(raiz_projeto):
    # O sandbox do Codex monta .agents/ somente leitura na raiz do workspace e precisa que ela exista:
    # com o repositório root:root, o nexo não consegue criá-la (bwrap: Can't mkdir .agents).
    assert (raiz_projeto / ".agents" / ".gitkeep").is_file()


def test_setup_reexecuta_com_volume_montado(raiz_projeto):
    # Montado sem allow_other, nem root enxerga /srv/casos: mkdir/chown nele quebram o setup.
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    assert 'sudo -u "${NEXO_USER}" mountpoint -q "${CASOS_DIR}"' in texto


def test_logrotate_da_auditoria(raiz_projeto):
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    bloco = texto[texto.index("/etc/logrotate.d/agencia-nexo"):]
    assert "su ${NEXO_USER} ${NEXO_USER}" in bloco  # diretório do nexo: sem `su` o logrotate recusa
    assert "rotate 60" in bloco and "monthly" in bloco  # 5 anos de auditoria


def test_operacao_documentada(raiz_projeto):
    texto = (raiz_projeto / "docs" / "OPERACAO.md").read_text(encoding="utf-8")
    for trecho in ("nexo-montar-casos", "codex login status", "nexo-egress-drop", "/var/log/agencia-nexo/auditoria.jsonl",
                   "caso arquivar", "--senha-arquivo", "caso desarquivar", "_arquivo", "ponte-nexo", "usage limit"):
        assert trecho in texto, trecho
    assert "ANTHROPIC_API_KEY" not in texto and "OPENAI_API_KEY" not in texto


def test_ambiente_local_nao_versionado(raiz_projeto):
    # .venv (diretório ou atalho) nunca entra no git: no CI o `uv sync` não conseguiria criá-lo.
    import subprocess as sp
    rastreados = sp.run(["git", "-C", str(raiz_projeto), "ls-files", ".venv"], capture_output=True, text=True).stdout
    assert rastreados.strip() == ""


@pytest.mark.parametrize("script", ["ops/setup_vps.sh", "ops/instalar_ponte.sh", "ops/teste_headless.sh",
                                    "ops/nexo_exec.sh", "hermes/nexo_run.sh"])
def test_heredoc_sem_aspas_nao_executa_nada(raiz_projeto, script):
    # Em heredoc sem aspas (<<EOF) o bash executa `...` e $(...): uma crase num comentário abriu um
    # shell root interativo durante o setup. Só heredoc com aspas (<<'EOF') pode conter isso.
    import re
    linhas = (raiz_projeto / script).read_text(encoding="utf-8").splitlines()
    dentro, fim = False, None
    for n, linha in enumerate(linhas, 1):
        if not dentro:
            m = re.search(r"<<-?\s*([A-Za-z_]+)\b", linha)  # sem aspas: <<EOF
            if m and not re.search(r"<<-?\s*['\"]", linha):
                dentro, fim = True, m.group(1)
            continue
        if linha.strip() == fim:
            dentro = False
            continue
        assert "`" not in linha and "$(" not in linha, f"{script}:{n}: {linha.strip()}"
