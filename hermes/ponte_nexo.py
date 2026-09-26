#!/usr/bin/env python3
"""hermes/ponte_nexo.py — ponte local entre o perfil Hermes `nexo` (container) e a Agência (usuário nexo).

Roda como `nexo` (systemd: hermes/ponte-nexo.service), escuta só em 127.0.0.1 e aceita
POST /acao {"acao": ..., "caso": ..., "pergunta": ...} com `Authorization: Bearer <token>`.

Ações: status | novo | diligencias (respondem na hora), ingerir | analisar (segundo plano, uma por caso),
resultado (estado da última execução longa). Tudo passa por /usr/local/bin/nexo_run.
Devolve só {"caso", "acao", "status", "result"}: nunca caminho, log ou conteúdo de /srv/casos.
Só biblioteca padrão.
"""
import dataclasses
import hmac
import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ACOES_CURTAS = ("status", "novo", "diligencias")
ACOES_LONGAS = ("ingerir", "analisar")
ACOES = (*ACOES_CURTAS, *ACOES_LONGAS, "resultado")
CODINOME = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
LIMITE_CORPO = 4096
TIMEOUT_CURTA = 900  # segundos


@dataclasses.dataclass
class Config:
    token: str
    nexo_run: str = "/usr/local/bin/nexo_run"
    casos: str = "/srv/casos"
    host: str = "127.0.0.1"
    porta: int = 8791


def _resposta(caso, acao, status, result):
    return {"caso": caso, "acao": acao, "status": status, "result": result}


def _ultima_execucao(casos: str, cod: str):
    """(arquivo .json, pronto?) da última execução longa do caso, ou (None, False)."""
    log = Path(casos) / cod / "log"
    runs = sorted(log.glob("run_*.json")) if log.is_dir() else []
    if not runs:
        return None, False
    ultimo = runs[-1]
    return ultimo, ultimo.with_suffix(".done").exists()


def executar(cfg: Config, acao: str, cod: str, pergunta: str) -> dict:
    if acao == "resultado":
        arq, pronto = _ultima_execucao(cfg.casos, cod)
        if arq is None:
            return _resposta(cod, acao, "sem_execucao", f"CASO {cod}: nenhuma análise registrada.")
        if not pronto:
            return _resposta(cod, acao, "em_execucao", f"CASO {cod}: análise em andamento.")
        try:
            dados = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dados = {}
        if dados.get("status") != "ok" or not dados.get("result"):
            return _resposta(cod, dados.get("acao", acao), "erro", f"CASO {cod}: execução falhou; ver log do caso na VPS.")
        return _resposta(cod, dados.get("acao", acao), "ok", dados["result"])

    if acao in ACOES_LONGAS:
        arq, pronto = _ultima_execucao(cfg.casos, cod)
        if arq is not None and not pronto:
            return _resposta(cod, acao, "em_execucao", f"CASO {cod}: já há uma análise em andamento; aguarde o resultado.")

    args = [cfg.nexo_run, acao, cod, *([pergunta] if pergunta else [])]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_CURTA,
                           env={**os.environ, "AGENCIA_CASOS": cfg.casos})
    except subprocess.TimeoutExpired:
        return _resposta(cod, acao, "erro", f"CASO {cod}: tempo esgotado; ver log do caso na VPS.")
    try:
        dados = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        dados = {}
    if dados.get("status") == "em_execucao":
        return _resposta(cod, acao, "em_execucao", f"CASO {cod}: em análise. Consulte com `resultado {cod}`.")
    if dados.get("erro"):
        return _resposta(cod, acao, "erro", f"CASO {cod}: {dados['erro']}")
    if dados.get("status") == "ok" and dados.get("result"):
        return _resposta(cod, acao, "ok", dados["result"])
    return _resposta(cod, acao, "erro", f"CASO {cod}: execução falhou; ver log do caso na VPS.")


def criar_servidor(cfg: Config) -> ThreadingHTTPServer:
    if cfg.host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("a ponte só escuta em localhost")
    if len(cfg.token or "") < 32:
        raise ValueError("token da ponte ausente ou curto (mínimo 32 caracteres)")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, codigo: int, corpo: dict) -> None:
            dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def do_POST(self):  # noqa: N802
            if self.path != "/acao":
                return self._json(404, {"erro": "rota desconhecida"})
            recebido = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(recebido.encode(), cfg.token.encode()):
                return self._json(401, {"erro": "não autorizado"})
            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho > LIMITE_CORPO:
                return self._json(413, {"erro": "pedido grande demais"})
            try:
                pedido = json.loads(self.rfile.read(tamanho) or b"{}")
            except ValueError:
                return self._json(400, {"erro": "JSON inválido"})
            acao = str(pedido.get("acao", ""))
            cod = str(pedido.get("caso", ""))
            pergunta = " ".join(str(pedido.get("pergunta", "") or "").split())[:500]
            if acao not in ACOES:
                return self._json(400, {"erro": f"ação inválida; use {', '.join(ACOES)}"})
            if not CODINOME.match(cod):
                return self._json(400, {"erro": "codinome inválido"})
            return self._json(200, executar(cfg, acao, cod, pergunta))

        def log_message(self, *args):  # sem log de pedidos: a auditoria fica na Agência
            pass

    return ThreadingHTTPServer((cfg.host, cfg.porta), Handler)


def main() -> None:
    arq = os.environ.get("AGENCIA_PONTE_TOKEN_ARQUIVO", "/etc/agencia-nexo/ponte.token")
    cfg = Config(
        token=Path(arq).read_text(encoding="utf-8").strip(),
        nexo_run=os.environ.get("NEXO_RUN", "/usr/local/bin/nexo_run"),
        casos=os.environ.get("AGENCIA_CASOS", "/srv/casos"),
        porta=int(os.environ.get("AGENCIA_PONTE_PORTA", "8791")),
    )
    criar_servidor(cfg).serve_forever()


if __name__ == "__main__":
    main()
