#!/usr/bin/env python3
"""PostToolUse: registra cada chamada de ferramenta em JSONL (entrada truncada)."""
import datetime
import json
import os
import sys

destino = os.environ.get("AGENCIA_AUDIT_LOG", "/var/log/agencia-nexo/auditoria.jsonl")
dados = json.load(sys.stdin)
registro = {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "sessao": dados.get("session_id"),
    "ferramenta": dados.get("tool_name"),
    "entrada": json.dumps(dados.get("tool_input", {}), ensure_ascii=False)[:500],
}
try:
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
except OSError as e:
    print(f"audit_log: falha ao gravar ({e})", file=sys.stderr)
sys.exit(0)
