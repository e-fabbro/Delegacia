#!/usr/bin/env python3
"""PreToolUse: bloqueia acesso do modelo a brutos, cofre e rede. Exit 2 = bloqueio.

Runtime Codex: shell chega como `Bash` (`command` string, às vezes lista ou embrulhado
em `bash -lc '...'`); edição chega como `apply_patch` com o patch inteiro em `command`.

Regras:
- apply_patch: inspeciona só os caminhos dos cabeçalhos (Add/Update/Delete File, Move to).
  O conteúdo escrito pode citar 00_brutos ou _cofre.
- Outras ferramentas com argumento de caminho (file_path, path, ...): inspeciona só o caminho.
- Bash: inspeciona o comando inteiro. Comando que toca caminho protegido só passa
  se for chamada direta a `python -m agencia`, sem encadeamento (; && || | ` $( )
  nem redirecionamento (> <) nem quebra de linha.
- Bash: qualquer cliente de rede é bloqueado.
"""
import datetime
import json
import os
import re
import shlex
import sys

# Segmento de caminho protegido, com ou sem barra antes (cobre caminho relativo).
SEGMENTO_PROTEGIDO = r"(?:^|[\s/\"'=:,(])(?:00_brutos|_cofre)(?:[/\s\"'),;]|$)"
ARQUIVO_PROTEGIDO = r"identidades[.]db|(?:^|[\s/\"'=])[.]env\b"
PROTEGIDOS = (SEGMENTO_PROTEGIDO, ARQUIVO_PROTEGIDO)
REDE = r"\b(curl|wget|nc|ncat|ssh|scp|rsync|ftp|telnet)\b"
ENCADEAMENTO = (";", "&&", "||", "|", "`", "$(", ">", "<", "\n")
PREFIXOS_OK = ("python -m agencia ", "uv run python -m agencia ")
CHAVES_CAMINHO = ("file_path", "path", "notebook_path", "pattern", "glob")
CABECALHO_PATCH = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", re.MULTILINE)
SHELLS = ("bash", "sh", "/bin/bash", "/bin/sh", "/usr/bin/bash")


CONTEXTO: dict = {}


def registrar_bloqueio(msg: str) -> None:
    """PostToolUse não dispara em chamada bloqueada: o bloqueio entra na auditoria aqui."""
    destino = os.environ.get("AGENCIA_AUDIT_LOG", "/var/log/agencia-nexo/auditoria.jsonl")
    registro = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sessao": CONTEXTO.get("session_id"),
        "ferramenta": CONTEXTO.get("tool_name"),
        "entrada": json.dumps(CONTEXTO.get("tool_input", {}), ensure_ascii=False)[:500],
        "bloqueado": True,
        "motivo": msg,
    }
    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        pass  # sem log não libera: o bloqueio vale do mesmo jeito


def bloquear(msg: str) -> None:
    registrar_bloqueio(msg)
    print(f"BLOQUEADO pela política da Agência Nexo: {msg}", file=sys.stderr)
    sys.exit(2)


def toca_protegido(texto: str) -> bool:
    return any(re.search(p, texto) for p in PROTEGIDOS)


def desembrulhar(comando) -> str:
    """Lista vira string; `bash -lc '<cmd>'` vira `<cmd>` (até 3 níveis)."""
    if isinstance(comando, list):
        partes = [str(x) for x in comando]
    else:
        texto = str(comando or "").strip()
        try:
            partes = shlex.split(texto)
        except ValueError:
            return texto
        if not (len(partes) == 3 and partes[0] in SHELLS and partes[1] in ("-c", "-lc")):
            return texto
    for _ in range(3):
        if len(partes) == 3 and partes[0] in SHELLS and partes[1] in ("-c", "-lc"):
            interno = partes[2].strip()
            try:
                partes = shlex.split(interno)
            except ValueError:
                return interno
            if not (len(partes) == 3 and partes[0] in SHELLS and partes[1] in ("-c", "-lc")):
                return interno
        else:
            break
    return shlex.join(partes)


def main() -> None:
    dados = json.load(sys.stdin)
    if isinstance(dados, dict):
        CONTEXTO.update({k: dados.get(k) for k in ("session_id", "tool_name", "tool_input")})
    ferramenta = dados.get("tool_name", "")
    entrada = dados.get("tool_input", {}) or {}

    if ferramenta == "Bash":
        cmd = desembrulhar(entrada.get("command", ""))
        if re.search(REDE, cmd):
            bloquear("acesso à rede não permitido aos agentes.")
        if toca_protegido(cmd):
            encadeado = any(s in cmd for s in ENCADEAMENTO)
            if not cmd.startswith(PREFIXOS_OK) or encadeado:
                bloquear("brutos e cofre só são acessados pelo pacote agencia, sem encadeamento.")
    elif ferramenta == "apply_patch":
        patch = str(entrada.get("command", "") or entrada.get("patch", "") or entrada.get("input", ""))
        for caminho in CABECALHO_PATCH.findall(patch):
            if toca_protegido(caminho.strip()):
                bloquear("apply_patch não pode tocar brutos, cofre nem segredos do projeto.")
    else:
        caminhos = " ".join(str(entrada.get(k, "") or "") for k in CHAVES_CAMINHO)
        if toca_protegido(caminhos):
            bloquear(f"{ferramenta} não pode tocar brutos, cofre nem segredos do projeto.")

    sys.exit(0)


if __name__ == "__main__":
    # Falha fechado: hook que quebra sai com 1, e o runtime trataria como "não bloqueou".
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        bloquear(f"entrada inesperada no hook ({type(e).__name__}); chamada recusada.")
