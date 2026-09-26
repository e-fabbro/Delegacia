"""E3: ponte local Hermes → Agência (hermes/ponte_nexo.py) e cliente do perfil (hermes/agencia_cliente.py).

A ponte roda como `nexo`, escuta só em 127.0.0.1, exige token e devolve só o resumo. Um `nexo_run` falso
faz o papel da Agência; nenhum Codex é chamado."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

TOKEN = "t" * 48
FALSO_RUN = r"""#!/usr/bin/env bash
ACAO="$1"; COD="$2"
echo "$@" >> "$REGISTRO"
case "$ACAO" in
  status|novo|diligencias)
    printf '{"status":"ok","caso":"%s","acao":"%s","result":"CASO %s\\nFase: novo"}\n' "$COD" "$ACAO" "$COD" ;;
  ingerir|analisar)
    mkdir -p "$AGENCIA_CASOS/$COD/log"
    S="$AGENCIA_CASOS/$COD/log/run_$(date +%s%N).json"
    : > "$S"
    echo "{\"status\":\"em_execucao\",\"caso\":\"$COD\",\"acao\":\"$ACAO\",\"resultado\":\"$S\",\"pronto\":\"${S%.json}.done\"}" ;;
esac
"""


def _carregar(raiz, nome):
    spec = importlib.util.spec_from_file_location(nome, raiz / "hermes" / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ponte(raiz_projeto, tmp_path):
    run = tmp_path / "nexo_run"
    run.write_text(FALSO_RUN)
    run.chmod(run.stat().st_mode | stat.S_IEXEC)
    casos = tmp_path / "casos"
    casos.mkdir()
    for cod in ("TESTE", "TESTE3"):
        (casos / cod).mkdir()
        (casos / cod / "estado.json").write_text("{}")
    os.environ["REGISTRO"] = str(tmp_path / "registro")
    mod = _carregar(raiz_projeto, "ponte_nexo")
    cfg = mod.Config(token=TOKEN, nexo_run=str(run), casos=str(casos), host="127.0.0.1", porta=0)
    srv = mod.criar_servidor(cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, casos, tmp_path
    srv.shutdown()


def chamar(srv, corpo, token=TOKEN, bruto=None):
    dados = bruto if bruto is not None else json.dumps(corpo).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/acao", data=dados, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_escuta_so_localhost(raiz_projeto):
    mod = _carregar(raiz_projeto, "ponte_nexo")
    assert mod.Config(token=TOKEN).host == "127.0.0.1"
    with pytest.raises(ValueError):
        mod.criar_servidor(mod.Config(token=TOKEN, host="0.0.0.0", porta=0))


def test_token_curto_recusado(raiz_projeto):
    mod = _carregar(raiz_projeto, "ponte_nexo")
    with pytest.raises(ValueError):
        mod.criar_servidor(mod.Config(token="curto", porta=0))


def test_sem_token_401(ponte):
    srv, _, _ = ponte
    assert chamar(srv, {"acao": "status", "caso": "TESTE"}, token="errado" * 8)[0] == 401


@pytest.mark.parametrize("corpo,trecho", [
    ({"acao": "apagar", "caso": "TESTE"}, "ação"),
    ({"acao": "status", "caso": "teste; rm -rf /"}, "codinome"),
    ({"acao": "status"}, "codinome"),
])
def test_entrada_invalida_400(ponte, corpo, trecho):
    srv, _, tmp = ponte
    codigo, resp = chamar(srv, corpo)
    assert codigo == 400 and trecho in resp["erro"]
    assert not (tmp / "registro").exists()  # a Agência nem foi chamada


def test_corpo_grande_recusado(ponte):
    srv, _, _ = ponte
    assert chamar(srv, None, bruto=b"{" + b" " * 20000 + b"}")[0] == 413


def test_status_devolve_so_o_resumo(ponte):
    srv, _, _ = ponte
    codigo, resp = chamar(srv, {"acao": "status", "caso": "TESTE"})
    assert codigo == 200
    assert resp == {"caso": "TESTE", "acao": "status", "status": "ok", "result": "CASO TESTE\nFase: novo"}


def test_analisar_em_segundo_plano_sem_caminhos(ponte):
    srv, _, tmp = ponte
    codigo, resp = chamar(srv, {"acao": "analisar", "caso": "TESTE3", "pergunta": "quem paga?"})
    assert codigo == 200 and resp["status"] == "em_execucao"
    assert "/" not in json.dumps(resp)  # nenhum caminho de /srv/casos sai pela ponte
    assert "analisar TESTE3 quem paga?" in (tmp / "registro").read_text()


def test_resultado_acompanha_execucao(ponte):
    srv, casos, _ = ponte
    assert chamar(srv, {"acao": "resultado", "caso": "TESTE3"})[1]["status"] == "sem_execucao"
    chamar(srv, {"acao": "analisar", "caso": "TESTE3"})
    assert chamar(srv, {"acao": "resultado", "caso": "TESTE3"})[1]["status"] == "em_execucao"
    run = sorted((casos / "TESTE3" / "log").glob("run_*.json"))[-1]
    run.write_text(json.dumps({"status": "ok", "caso": "TESTE3", "acao": "analisar", "result": "CASO TESTE3 · fase X"}))
    run.with_suffix(".done").touch()
    codigo, resp = chamar(srv, {"acao": "resultado", "caso": "TESTE3"})
    assert resp == {"caso": "TESTE3", "acao": "analisar", "status": "ok", "result": "CASO TESTE3 · fase X"}


def test_uma_execucao_longa_por_caso(ponte):
    srv, _, tmp = ponte
    chamar(srv, {"acao": "analisar", "caso": "TESTE3"})
    codigo, resp = chamar(srv, {"acao": "ingerir", "caso": "TESTE3"})
    assert resp["status"] == "em_execucao" and "já" in resp["result"]
    assert (tmp / "registro").read_text().count("TESTE3") == 1


def test_falha_da_execucao_nao_vaza_log(ponte):
    srv, casos, _ = ponte
    chamar(srv, {"acao": "analisar", "caso": "TESTE3"})
    run = sorted((casos / "TESTE3" / "log").glob("run_*.json"))[-1]
    run.write_text("")  # nexo_exec morreu sem JSON
    run.with_suffix(".done").touch()
    resp = chamar(srv, {"acao": "resultado", "caso": "TESTE3"})[1]
    assert resp["status"] == "erro" and "log" in resp["result"] and "/" not in resp["result"]


# ---------- cliente do perfil Hermes ----------

def test_cliente_fala_com_a_ponte(ponte, raiz_projeto, tmp_path):
    srv, _, _ = ponte
    arq_token = tmp_path / "token"
    arq_token.write_text(TOKEN + "\n")
    env = {"AGENCIA_PONTE_URL": f"http://127.0.0.1:{srv.server_address[1]}", "AGENCIA_PONTE_TOKEN_ARQUIVO": str(arq_token),
           "PATH": "/usr/bin:/bin"}
    cli = [sys.executable, str(raiz_projeto / "hermes" / "agencia_cliente.py")]
    p = subprocess.run([*cli, "status", "TESTE"], env=env, capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.strip() == "CASO TESTE\nFase: novo"
    p = subprocess.run([*cli, "analisar", "TESTE3", "quem", "paga?"], env=env, capture_output=True, text=True)
    assert p.returncode == 0 and "em análise" in p.stdout
    p = subprocess.run([*cli, "apagar", "TESTE"], env=env, capture_output=True, text=True)
    assert p.returncode == 2


def test_cliente_sem_ponte_da_erro_claro(raiz_projeto, tmp_path):
    arq_token = tmp_path / "token"
    arq_token.write_text(TOKEN)
    env = {"AGENCIA_PONTE_URL": "http://127.0.0.1:9", "AGENCIA_PONTE_TOKEN_ARQUIVO": str(arq_token), "PATH": "/usr/bin:/bin"}
    p = subprocess.run([sys.executable, str(raiz_projeto / "hermes" / "agencia_cliente.py"), "status", "TESTE"],
                       env=env, capture_output=True, text=True)
    assert p.returncode == 1 and "ponte" in p.stdout.lower()


def test_unidade_systemd_da_ponte(raiz_projeto):
    texto = (raiz_projeto / "hermes" / "ponte-nexo.service").read_text(encoding="utf-8")
    assert "User=nexo" in texto and "/opt/agencia-nexo/hermes/ponte_nexo.py" in texto
    assert "AGENCIA_PONTE_TOKEN_ARQUIVO=/etc/agencia-nexo/ponte.token" in texto
    assert "User=root" not in texto


def test_adendo_usa_so_a_ferramenta(raiz_projeto):
    texto = (raiz_projeto / "hermes" / "skills" / "agencia-nexo" / "SKILL.md").read_text(encoding="utf-8")
    assert "scripts/agencia <acao> <CODINOME>" in texto and "agencia resultado <COD>" in texto
    assert "sudo" not in texto  # o Hermes roda em container: a ponte substitui o sudoers
    assert "Só codinome, pseudônimos" in texto


def test_skill_no_formato_do_hermes(raiz_projeto):
    texto = (raiz_projeto / "hermes" / "skills" / "agencia-nexo" / "SKILL.md").read_text(encoding="utf-8")
    assert texto.startswith("---\nname: agencia-nexo\n")
    assert "ponte da Gutcha" in texto


def test_instalador_da_ponte(raiz_projeto):
    texto = (raiz_projeto / "ops" / "instalar_ponte.sh").read_text(encoding="utf-8")
    assert texto.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in texto
    assert "install -m 0755" in texto and "/usr/local/bin/nexo_run" in texto
    assert "secrets.token_urlsafe" in texto and "chmod 0640 /etc/agencia-nexo/ponte.token" in texto
    assert "chown root:nexo /etc/agencia-nexo/ponte.token" in texto
    assert '"${PERFIL}/scripts/.ponte_token"' in texto and "-m 0600" in texto
    assert '"${PERFIL}/skills/agencia-nexo/SKILL.md"' in texto
    assert "skill `agencia-nexo`" in texto  # linha acrescentada ao SOUL.md, idempotente
    assert "ponte-nexo.service" in texto
    assert "sudoers" not in texto
    p = subprocess.run(["bash", "-n", str(raiz_projeto / "ops" / "instalar_ponte.sh")], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
