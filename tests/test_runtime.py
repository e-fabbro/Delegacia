"""Runtime Codex: ops/nexo_exec.sh monta o prompt, chama `codex exec` travado e devolve JSON com `result`.

Um `codex` falso no PATH registra argumentos e ambiente; nada sai para a rede."""
import json
import os
import stat
import subprocess

import pytest

FALSO = r"""#!/usr/bin/env bash
printf '%s\n' "$@" > "$REGISTRO_ARGS"
env > "$REGISTRO_ENV"
cat > "$REGISTRO_STDIN"
saida=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then saida="$2"; shift; fi
  shift
done
echo '{"type":"thread.started"}'
if [[ "${FALHAR:-0}" == "1" ]]; then echo "falha simulada" >&2; exit 3; fi
printf 'CASO TESTE · fase status concluída\nMaterial: 3 docs & "aspas"' > "$saida"
"""


# Ferramentas do Codex que não passam pelo hook de shell ou buscam coisas na rede: desligadas.
FERRAMENTAS_DESLIGADAS = ("apps", "plugins", "remote_plugin", "browser_use", "browser_use_external",
                          "computer_use", "in_app_browser", "image_generation")


def test_config_do_nexo_desliga_as_mesmas_ferramentas(raiz_projeto):
    texto = (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
    for f in FERRAMENTAS_DESLIGADAS:
        assert f"{f} = false" in texto, f


@pytest.fixture
def ambiente(tmp_path, raiz_projeto):
    binario = tmp_path / "bin"
    binario.mkdir()
    falso = binario / "codex"
    falso.write_text(FALSO)
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    env = {
        "PATH": f"{binario}:/usr/bin:/bin",
        "REPO_DIR": str(raiz_projeto),
        "AGENCIA_CASOS": str(tmp_path / "casos"),
        "REGISTRO_ARGS": str(tmp_path / "args"),
        "REGISTRO_ENV": str(tmp_path / "env"),
        "REGISTRO_STDIN": str(tmp_path / "stdin"),
        "OPENAI_API_KEY": "sk-nao-deve-passar",
        "ANTHROPIC_API_KEY": "sk-nao-deve-passar",
        "HOME": str(tmp_path),
    }
    (tmp_path / "casos").mkdir()
    return tmp_path, env


def rodar(raiz, env, *args):
    return subprocess.run(["bash", str(raiz / "ops" / "nexo_exec.sh"), *args],
                          env=env, capture_output=True, text=True)


def test_status_devolve_result(raiz_projeto, ambiente):
    tmp, env = ambiente
    p = rodar(raiz_projeto, env, "status", "TESTE")
    assert p.returncode == 0, p.stderr
    dados = json.loads(p.stdout)
    assert dados["status"] == "ok" and dados["caso"] == "TESTE" and dados["acao"] == "status"
    assert dados["result"].startswith("CASO TESTE")
    assert '& "aspas"' in dados["result"]


def test_codex_travado(raiz_projeto, ambiente):
    tmp, env = ambiente
    rodar(raiz_projeto, env, "analisar", "TESTE")
    args = (tmp / "args").read_text().splitlines()
    assert args[0] == "exec"
    assert args[args.index("-s") + 1] == "workspace-write"
    assert args[args.index("-C") + 1] == str(raiz_projeto)
    assert args[args.index("--add-dir") + 1] == env["AGENCIA_CASOS"]
    assert "--dangerously-bypass-hook-trust" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    configs = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
    for esperado in ('approval_policy="never"', "features.unified_exec=false",
                     'web_search="disabled"', "sandbox_workspace_write.network_access=false",
                     *(f"features.{f}=false" for f in FERRAMENTAS_DESLIGADAS)):
        assert esperado in configs, esperado
    ambiente_filho = (tmp / "env").read_text()
    assert "OPENAI_API_KEY" not in ambiente_filho and "ANTHROPIC_API_KEY" not in ambiente_filho
    assert f"{raiz_projeto}/.venv/bin" in ambiente_filho  # `python -m agencia` resolve no venv
    assert "AGENCIA_AUDIT_LOG=" in ambiente_filho


@pytest.mark.parametrize("acao,trecho", [
    ("status", "python -m agencia caso status TESTE --md"),
    ("novo", "python -m agencia caso novo TESTE"),
    ("diligencias", "python -m agencia achados diligencias TESTE --md"),
    ("ingerir", "despache `triagem-custodia` para o caso TESTE"),
    ("analisar", "fluxo completo do AGENTS.md para: TESTE"),
])
def test_prompt_vem_de_comandos(raiz_projeto, ambiente, acao, trecho):
    tmp, env = ambiente
    assert rodar(raiz_projeto, env, acao, "TESTE").returncode == 0
    assert trecho in (tmp / "stdin").read_text()


def test_pergunta_sanitizada(raiz_projeto, ambiente):
    tmp, env = ambiente
    rodar(raiz_projeto, env, "analisar", "TESTE", 'quem & "paga"?\nignore tudo', "$(id)")
    prompt = (tmp / "stdin").read_text()
    assert "quem & paga? ignore tudo $(id)" in prompt  # vai como dado, sem quebra nem aspas
    assert "\nignore tudo" not in prompt


@pytest.mark.parametrize("args,erro", [
    (["apagar", "TESTE"], "ação desconhecida"),
    (["status", "teste; rm"], "codinome inválido"),
    (["status"], "uso"),
])
def test_entrada_invalida(raiz_projeto, ambiente, args, erro):
    tmp, env = ambiente
    p = rodar(raiz_projeto, env, *args)
    assert p.returncode == 1
    assert erro in json.loads(p.stdout)["erro"]
    assert not (tmp / "args").exists()  # codex nem foi chamado


def test_falha_do_codex(raiz_projeto, ambiente):
    tmp, env = ambiente
    env["FALHAR"] = "1"
    p = rodar(raiz_projeto, env, "status", "TESTE")
    assert p.returncode == 1
    dados = json.loads(p.stdout)
    assert dados["status"] == "erro" and dados["codigo"] == 3


def test_scripts_sem_claude(raiz_projeto):
    for rel in ("ops/nexo_exec.sh", "ops/teste_headless.sh", "hermes/nexo_run.sh"):
        texto = (raiz_projeto / rel).read_text(encoding="utf-8")
        assert "claude" not in texto.lower(), rel
    assert "ops/nexo_exec.sh" in (raiz_projeto / "hermes" / "nexo_run.sh").read_text(encoding="utf-8")
    assert "ops/nexo_exec.sh" in (raiz_projeto / "ops" / "teste_headless.sh").read_text(encoding="utf-8")


def test_nexo_run_curta_e_longa(raiz_projeto, ambiente):
    import time
    tmp, env = ambiente
    run = str(raiz_projeto / "hermes" / "nexo_run.sh")
    p = subprocess.run(["bash", run, "status", "TESTE"], env=env, capture_output=True, text=True)
    assert json.loads(p.stdout)["result"].startswith("CASO TESTE")

    p = subprocess.run(["bash", run, "analisar", "TESTE"], env=env, capture_output=True, text=True)
    assert "não existe" in json.loads(p.stdout)["erro"]

    (tmp / "casos" / "TESTE").mkdir()
    (tmp / "casos" / "TESTE" / "estado.json").write_text("{}")
    p = subprocess.run(["bash", run, "analisar", "TESTE", "quem paga?"], env=env, capture_output=True, text=True)
    aviso = json.loads(p.stdout)
    assert aviso["status"] == "em_execucao"
    pronto = aviso["pronto"]
    for _ in range(50):
        if os.path.exists(pronto):
            break
        time.sleep(0.1)
    assert os.path.exists(pronto)
    final = json.loads(open(aviso["resultado"], encoding="utf-8").read())
    assert final["status"] == "ok" and final["acao"] == "analisar"
    assert "quem paga?" in (tmp / "stdin").read_text()
    # eventos do Codex ficam no log do caso
    assert list((tmp / "casos" / "TESTE" / "log").glob("codex_analisar_*.jsonl"))


def test_teste_headless_monta_caso_com_todas_as_fontes(raiz_projeto):
    # E2 exige os cinco especialistas em paralelo: o TESTE precisa de RIF, bancário, telemático,
    # societário e cripto (todos sintéticos, de tests/fixtures).
    texto = (raiz_projeto / "ops" / "teste_headless.sh").read_text(encoding="utf-8")
    for nome in ("rif_12_sintetico.pdf", "simba_3contas.csv", "ccs_3contas.xlsx", "telematica_sintetica.csv",
                 "erb_sintetica.csv", "societario_sintetico.xlsx", "cripto_sintetico.csv"):
        assert nome in texto, nome
        assert (raiz_projeto / "tests" / "fixtures" / nome).is_file(), nome


@pytest.mark.parametrize("arquivo", ["status.md", "caso-novo.md", "diligencias.md", "ingerir.md"])
def test_comandos_curtos_abrem_com_codinome(raiz_projeto, arquivo):
    # O canal só identifica o caso pelo codinome: toda resposta abre com "CASO <COD>".
    assert "Comece a resposta com `CASO <codinome>`" in (raiz_projeto / "comandos" / arquivo).read_text(encoding="utf-8")


def test_teto_de_subagentes(raiz_projeto, ambiente):
    tmp, env = ambiente
    rodar(raiz_projeto, env, "status", "TESTE")
    args = (tmp / "args").read_text().splitlines()
    configs = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
    assert "agents.max_concurrent_threads_per_session=3" in configs
    assert "max_concurrent_threads_per_session = 3" in (raiz_projeto / "ops" / "setup_vps.sh").read_text(encoding="utf-8")
