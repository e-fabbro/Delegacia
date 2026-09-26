"""Critério de aceite F0: hooks bloqueiam brutos/cofre/rede e registram auditoria."""
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
