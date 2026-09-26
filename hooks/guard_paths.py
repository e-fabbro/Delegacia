#!/usr/bin/env python3
"""PreToolUse: bloqueia acesso do modelo a brutos, cofre e rede. Exit 2 = bloqueio.

Regras:
- Ferramentas de arquivo (Read, Edit, Write, Grep, Glob, ...): inspeciona só os
  argumentos de caminho. O conteúdo escrito pode citar 00_brutos ou _cofre.
- Bash: inspeciona o comando inteiro. Comando que toca caminho protegido só passa
  se for chamada direta a `python -m agencia`, sem encadeamento (; && || | ` $( )
  nem redirecionamento (> <) nem quebra de linha.
- Bash: qualquer cliente de rede é bloqueado.
"""
import json
import re
import sys

# Segmento de caminho protegido, com ou sem barra antes (cobre caminho relativo).
SEGMENTO_PROTEGIDO = r"(?:^|[\s/\"'=:,(])(?:00_brutos|_cofre)(?:[/\s\"'),;]|$)"
ARQUIVO_PROTEGIDO = r"identidades[.]db|(?:^|[\s/\"'=])[.]env\b"
PROTEGIDOS = (SEGMENTO_PROTEGIDO, ARQUIVO_PROTEGIDO)
REDE = r"\b(curl|wget|nc|ncat|ssh|scp|rsync|ftp|telnet)\b"
ENCADEAMENTO = (";", "&&", "||", "|", "`", "$(", ">", "<", "\n")
PREFIXOS_OK = ("python -m agencia ", "uv run python -m agencia ")
CHAVES_CAMINHO = ("file_path", "path", "notebook_path", "pattern", "glob")


def bloquear(msg: str) -> None:
    print(f"BLOQUEADO pela política da Agência Nexo: {msg}", file=sys.stderr)
    sys.exit(2)


def toca_protegido(texto: str) -> bool:
    return any(re.search(p, texto) for p in PROTEGIDOS)


def main() -> None:
    dados = json.load(sys.stdin)
    ferramenta = dados.get("tool_name", "")
    entrada = dados.get("tool_input", {}) or {}

    if ferramenta == "Bash":
        cmd = (entrada.get("command", "") or "").strip()
        if re.search(REDE, cmd):
            bloquear("acesso à rede não permitido aos agentes.")
        if toca_protegido(cmd):
            encadeado = any(s in cmd for s in ENCADEAMENTO)
            if not cmd.startswith(PREFIXOS_OK) or encadeado:
                bloquear("brutos e cofre só são acessados pelo pacote agencia, sem encadeamento.")
    else:
        caminhos = " ".join(str(entrada.get(k, "") or "") for k in CHAVES_CAMINHO)
        if toca_protegido(caminhos):
            bloquear(f"{ferramenta} não pode tocar brutos, cofre nem segredos do projeto.")

    sys.exit(0)


if __name__ == "__main__":
    main()
