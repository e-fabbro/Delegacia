"""Cadeia de custódia (CPP arts. 158-A a 158-F) em `01_custodia/cadeia.jsonl`.

Cada evento registra: etapa (158-B), quando (UTC), quem (usuário Unix e host), o quê
(descrição) e o hash do vestígio. Ponto de substituição pelo `custodia.py` do Fabbro:
manter a assinatura de `registrar`.
"""
import datetime as dt
import getpass
import hashlib
import json
import socket
from pathlib import Path

ETAPAS_158B = (
    "reconhecimento",
    "isolamento",
    "fixacao",
    "coleta",
    "acondicionamento",
    "transporte",
    "recebimento",
    "processamento",
    "armazenamento",
    "descarte",
)


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def sha256_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def registrar(caso_dir: Path, doc_id: str, etapa: str, descricao: str, sha256: str, extra: dict | None = None) -> dict:
    if etapa not in ETAPAS_158B:
        raise ValueError(f"etapa de custódia desconhecida: {etapa}")
    evento = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "doc_id": doc_id,
        "etapa": etapa,
        "responsavel": getpass.getuser(),
        "host": socket.gethostname(),
        "descricao": descricao,
        "sha256": sha256,
    }
    if extra:
        evento.update(extra)
    arq = caso_dir / "01_custodia" / "cadeia.jsonl"
    with open(arq, "a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")
    return evento


def ler(caso_dir: Path) -> list[dict]:
    arq = caso_dir / "01_custodia" / "cadeia.jsonl"
    if not arq.is_file():
        return []
    return [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()]
