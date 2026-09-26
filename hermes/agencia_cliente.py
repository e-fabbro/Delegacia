#!/usr/bin/env python3
"""hermes/agencia_cliente.py — ferramenta do perfil Hermes `nexo` para acionar a Agência pela ponte local.

Instalar em /root/.hermes/profiles/nexo/scripts/agencia (755). Uso pelo Nexo (Hermes):
    agencia <status|novo|ingerir|analisar|diligencias|resultado> <CODINOME> [pergunta...]
Imprime só o resumo (codinome, pseudônimos, contagens, status). Só biblioteca padrão.
Saída: 0 ok/em andamento, 1 falha da Agência ou da ponte, 2 uso errado.
"""
import json
import os
import sys
import urllib.error
import urllib.request

ACOES = ("status", "novo", "ingerir", "analisar", "diligencias", "resultado")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ACOES:
        print(f"uso: agencia <{'|'.join(ACOES)}> <CODINOME> [pergunta]")
        return 2
    acao, cod, pergunta = argv[0], argv[1], " ".join(argv[2:])
    url = os.environ.get("AGENCIA_PONTE_URL", "http://127.0.0.1:8791").rstrip("/") + "/acao"
    arq_token = os.environ.get("AGENCIA_PONTE_TOKEN_ARQUIVO",
                               os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ponte_token"))
    try:
        token = open(arq_token, encoding="utf-8").read().strip()
    except OSError:
        print("Agência indisponível: token da ponte não encontrado.")
        return 1
    corpo = json.dumps({"acao": acao, "caso": cod, "pergunta": pergunta}).encode()
    req = urllib.request.Request(url, data=corpo, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=960) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            print(f"Agência recusou o pedido: {json.loads(e.read()).get('erro', e.code)}")
        except ValueError:
            print(f"Agência recusou o pedido ({e.code}).")
        return 2 if e.code == 400 else 1
    except (urllib.error.URLError, OSError):
        print("Agência indisponível: a ponte local não respondeu.")
        return 1
    if resp.get("status") == "em_execucao":
        print(f"Caso {cod} em análise. Consulte depois com: agencia resultado {cod}")
        return 0
    print(resp.get("result", ""))
    return 0 if resp.get("status") in ("ok", "sem_execucao") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
