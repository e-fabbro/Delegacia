"""Critério de aceite F0: hooks bloqueiam brutos/cofre/rede e registram auditoria.

O runtime é o Codex: shell chega como `Bash` com `command` string; edição chega como
`apply_patch` com o patch inteiro em `command`."""
import json
import subprocess
import sys

import pytest

CASO = "/srv/casos/TESTE"


def rodar_hook(raiz_projeto, nome, tool_name, tool_input, env=None):
    proc = subprocess.run(
        [sys.executable, str(raiz_projeto / "hooks" / nome)],
        input=json.dumps({"session_id": "s1", "tool_name": tool_name, "tool_input": tool_input}),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc


# ---------- guard_paths: bloqueios ----------

BLOQUEADOS = [
    ("Read", {"file_path": f"{CASO}/00_brutos/rif.pdf"}),
    ("Read", {"file_path": f"{CASO}/_cofre/identidades.db"}),
    ("Grep", {"pattern": "CPF", "path": f"{CASO}/00_brutos"}),
    ("Glob", {"pattern": f"{CASO}/00_brutos/*.pdf"}),
    ("Edit", {"file_path": f"{CASO}/00_brutos/rif.pdf", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": f"{CASO}/_cofre/x.txt", "content": "x"}),
    ("Read", {"file_path": "/opt/agencia-nexo/.env"}),
    ("Bash", {"command": f"cat {CASO}/00_brutos/rif.pdf"}),
    ("Bash", {"command": f"ls {CASO}/_cofre"}),
    ("Bash", {"command": f"sqlite3 {CASO}/_cofre/identidades.db .tables"}),
    ("Bash", {"command": f"cd {CASO} && cat 00_brutos/rif.pdf"}),  # caminho relativo
    ("Bash", {"command": "cat 00_brutos/rif.pdf"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE && cat {CASO}/00_brutos/rif.pdf"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE; cat {CASO}/00_brutos/rif.pdf"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE || cat {CASO}/00_brutos/rif.pdf"}),
    ("Bash", {"command": f"python -m agencia caso status TESTE | tee {CASO}/00_brutos/x"}),
    ("Bash", {"command": f"python -m agencia caso status TESTE > {CASO}/00_brutos/x"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE < {CASO}/00_brutos/x"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE $(cat {CASO}/00_brutos/x)"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE `cat {CASO}/00_brutos/x`"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE\ncat {CASO}/00_brutos/x"}),
    ("Bash", {"command": "curl https://example.com"}),
    ("Bash", {"command": "wget -q https://example.com"}),
    ("Bash", {"command": "ssh nexo@host"}),
    ("Bash", {"command": "python -m agencia caso status TESTE && curl https://example.com"}),
]


@pytest.mark.parametrize("tool_name,tool_input", BLOQUEADOS)
def test_guard_bloqueia(raiz_projeto, tool_name, tool_input):
    proc = rodar_hook(raiz_projeto, "guard_paths.py", tool_name, tool_input)
    assert proc.returncode == 2, proc.stderr
    assert "BLOQUEADO" in proc.stderr


# ---------- guard_paths: permitidos ----------

PERMITIDOS = [
    ("Read", {"file_path": f"{CASO}/02_extraido/DOC-001.md"}),
    ("Read", {"file_path": f"{CASO}/estado.json"}),
    ("Grep", {"pattern": "PF-0003", "path": f"{CASO}/02_extraido"}),
    # conteúdo pode citar diretórios protegidos; só o caminho alvo importa
    ("Write", {"file_path": f"{CASO}/03_analises/analista-rif/nota.md", "content": "material em 00_brutos/ e _cofre/"}),
    ("Edit", {"file_path": f"{CASO}/04_produtos/x.md", "old_string": "00_brutos", "new_string": "_cofre"}),
    ("Bash", {"command": "python -m agencia caso status TESTE --md"}),
    ("Bash", {"command": "python -m agencia ingerir TESTE"}),
    ("Bash", {"command": f"python -m agencia ingerir TESTE --dir {CASO}/00_brutos"}),
    ("Bash", {"command": f"uv run python -m agencia ingerir TESTE --dir {CASO}/00_brutos/"}),
    ("Bash", {"command": "python -m agencia cofre vazamento TESTE"}),
    ("Bash", {"command": f"ls {CASO}/02_extraido"}),
    ("Bash", {"command": f"cat {CASO}/03_analises/analista-rif/nota.md | head -80"}),
    ("Bash", {"command": "ls /opt/agencia-nexo/config/layouts"}),
]


@pytest.mark.parametrize("tool_name,tool_input", PERMITIDOS)
def test_guard_permite(raiz_projeto, tool_name, tool_input):
    proc = rodar_hook(raiz_projeto, "guard_paths.py", tool_name, tool_input)
    assert proc.returncode == 0, proc.stderr


# ---------- Codex: apply_patch e shell embrulhado ----------

def _patch(*cabecalhos, corpo="+linha"):
    linhas = ["*** Begin Patch"]
    for c in cabecalhos:
        linhas += [c, corpo]
    linhas.append("*** End Patch")
    return "\n".join(linhas)


BLOQUEADOS_CODEX = [
    ("apply_patch", {"command": _patch(f"*** Add File: {CASO}/00_brutos/x.txt")}),
    ("apply_patch", {"command": _patch(f"*** Update File: {CASO}/_cofre/mapa.json")}),
    ("apply_patch", {"command": _patch(f"*** Delete File: {CASO}/00_brutos/rif.pdf")}),
    ("apply_patch", {"command": _patch("*** Add File: 00_brutos/x.txt")}),  # relativo
    ("apply_patch", {"command": _patch(f"*** Update File: {CASO}/03_analises/a.md\n*** Move to: {CASO}/_cofre/a.md")}),
    ("apply_patch", {"command": _patch(f"*** Add File: {CASO}/03_analises/ok.md", f"*** Add File: {CASO}/_cofre/y")}),
    ("apply_patch", {"command": _patch("*** Update File: /opt/agencia-nexo/.env")}),
    ("Bash", {"command": f"bash -lc 'cat {CASO}/00_brutos/rif.pdf'"}),
    ("Bash", {"command": f"bash -lc 'python -m agencia ingerir TESTE; cat {CASO}/00_brutos/x'"}),
    ("Bash", {"command": ["bash", "-lc", f"cat {CASO}/_cofre/identidades.db"]}),
    ("Bash", {"command": ["cat", f"{CASO}/00_brutos/rif.pdf"]}),
    ("Bash", {"command": "bash -lc 'curl https://example.com'"}),
]


@pytest.mark.parametrize("tool_name,tool_input", BLOQUEADOS_CODEX)
def test_guard_bloqueia_codex(raiz_projeto, tool_name, tool_input):
    proc = rodar_hook(raiz_projeto, "guard_paths.py", tool_name, tool_input)
    assert proc.returncode == 2, proc.stderr
    assert "BLOQUEADO" in proc.stderr


PERMITIDOS_CODEX = [
    ("apply_patch", {"command": _patch(f"*** Add File: {CASO}/03_analises/analista-rif/nota.md",
                                       corpo="+material em 00_brutos/ e _cofre/")}),
    ("apply_patch", {"command": _patch(f"*** Update File: {CASO}/04_produtos/x.md", corpo="-00_brutos\n+_cofre")}),
    ("Bash", {"command": "bash -lc 'python -m agencia caso status TESTE --md'"}),
    ("Bash", {"command": f"bash -lc 'python -m agencia ingerir TESTE --dir {CASO}/00_brutos'"}),
    ("Bash", {"command": ["bash", "-lc", f"uv run python -m agencia ingerir TESTE --dir {CASO}/00_brutos/"]}),
    ("Bash", {"command": ["ls", f"{CASO}/02_extraido"]}),
    ("update_plan", {"plan": [{"step": "ler 00_brutos? não", "status": "pending"}]}),
]


@pytest.mark.parametrize("tool_name,tool_input", PERMITIDOS_CODEX)
def test_guard_permite_codex(raiz_projeto, tool_name, tool_input):
    proc = rodar_hook(raiz_projeto, "guard_paths.py", tool_name, tool_input)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("entrada", ["nao-json", "[]", '{"tool_name": "Bash", "tool_input": 7}'])
def test_guard_falha_fechado(raiz_projeto, entrada):
    proc = subprocess.run([sys.executable, str(raiz_projeto / "hooks" / "guard_paths.py")],
                          input=entrada, capture_output=True, text=True)
    assert proc.returncode == 2, proc.stderr
    assert "BLOQUEADO" in proc.stderr


def test_hooks_json_do_codex(raiz_projeto):
    cfg = json.loads((raiz_projeto / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    pre = cfg["PreToolUse"][0]
    assert pre["matcher"] in ("*", None) or "Bash" in pre["matcher"] and "apply_patch" in pre["matcher"]
    assert "hooks/guard_paths.py" in pre["hooks"][0]["command"]
    assert "hooks/audit_log.py" in cfg["PostToolUse"][0]["hooks"][0]["command"]
    # sem variável de projeto do Claude; caminho resolvido a partir do repositório
    assert "CLAUDE_PROJECT_DIR" not in json.dumps(cfg)


# ---------- audit_log ----------

def test_audit_log_registra(raiz_projeto, tmp_path):
    destino = tmp_path / "log" / "auditoria.jsonl"
    env = {"AGENCIA_AUDIT_LOG": str(destino), "PATH": "/usr/bin:/bin"}
    proc = rodar_hook(raiz_projeto, "audit_log.py", "Bash", {"command": "python -m agencia caso status TESTE"}, env=env)
    assert proc.returncode == 0, proc.stderr
    linhas = destino.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1
    registro = json.loads(linhas[0])
    assert registro["ferramenta"] == "Bash"
    assert registro["sessao"] == "s1"
    assert "caso status TESTE" in registro["entrada"]
    assert registro["ts"].endswith("+00:00")


def test_audit_log_trunca_entrada(raiz_projeto, tmp_path):
    destino = tmp_path / "auditoria.jsonl"
    env = {"AGENCIA_AUDIT_LOG": str(destino), "PATH": "/usr/bin:/bin"}
    rodar_hook(raiz_projeto, "audit_log.py", "Write", {"file_path": "x", "content": "a" * 5000}, env=env)
    registro = json.loads(destino.read_text(encoding="utf-8").splitlines()[0])
    assert len(registro["entrada"]) <= 500


def test_bloqueio_fica_na_auditoria(raiz_projeto, tmp_path):
    # PostToolUse não dispara em chamada bloqueada: o guard registra o bloqueio (critério 4).
    destino = tmp_path / "auditoria.jsonl"
    env = {"AGENCIA_AUDIT_LOG": str(destino), "PATH": "/usr/bin:/bin"}
    proc = rodar_hook(raiz_projeto, "guard_paths.py", "Bash", {"command": f"cat {CASO}/00_brutos/rif.pdf"}, env=env)
    assert proc.returncode == 2
    registro = json.loads(destino.read_text(encoding="utf-8").splitlines()[0])
    assert registro["bloqueado"] is True and registro["ferramenta"] == "Bash" and registro["sessao"] == "s1"
    assert "00_brutos" in registro["entrada"] and registro["motivo"]


def test_permitido_nao_duplica_auditoria(raiz_projeto, tmp_path):
    destino = tmp_path / "auditoria.jsonl"
    env = {"AGENCIA_AUDIT_LOG": str(destino), "PATH": "/usr/bin:/bin"}
    rodar_hook(raiz_projeto, "guard_paths.py", "Bash", {"command": "python -m agencia caso status TESTE"}, env=env)
    assert not destino.exists()
