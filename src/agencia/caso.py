"""Ciclo de vida de um caso: criação da estrutura, status e estado (estado.json)."""
import datetime as dt
import json
import os
import re
from pathlib import Path

from agencia import db

CASOS_PADRAO = Path("/srv/casos")
CODINOME_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
FASES = ("novo", "ingestao", "analise", "integracao", "redacao", "concluido", "arquivado")
SUBDIRS = (
    "00_brutos",
    "01_custodia",
    "02_extraido",
    "03_analises",
    "04_produtos",
    "04_produtos/render",
    "_cofre",
    "log",
)
CHAVES_PROTEGIDAS = ("codinome", "criado_em", "atualizado_em")


class ErroCaso(Exception):
    """Erro de uso reportável ao operador (vira {"erro": ...} na CLI)."""


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def raiz_casos() -> Path:
    return Path(os.environ.get("AGENCIA_CASOS") or CASOS_PADRAO)


def validar_codinome(codinome: str) -> str:
    if not codinome or not CODINOME_RE.match(codinome):
        raise ErroCaso(
            f"codinome inválido: {codinome!r} (use MAIÚSCULAS, dígitos, _ ou -, 2 a 32 caracteres, iniciando por letra)"
        )
    return codinome


def caminho(codinome: str, deve_existir: bool = True) -> Path:
    d = raiz_casos() / validar_codinome(codinome)
    if deve_existir and not (d / "estado.json").is_file():
        raise ErroCaso(f"caso {codinome} não existe em {raiz_casos()}")
    return d


# ---------- estado.json ----------

def _ler_estado(d: Path) -> dict:
    return json.loads((d / "estado.json").read_text(encoding="utf-8"))


def _gravar_estado(d: Path, estado: dict) -> None:
    estado["atualizado_em"] = agora()
    tmp = d / "estado.json.tmp"
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(d / "estado.json")


# ---------- caso novo ----------

def novo(codinome: str) -> dict:
    validar_codinome(codinome)
    raiz = raiz_casos()
    d = raiz / codinome
    if d.exists():
        raise ErroCaso(f"caso {codinome} já existe em {raiz}")

    raiz.mkdir(parents=True, exist_ok=True)
    d.mkdir(mode=0o700)
    for sub in SUBDIRS:
        (d / sub).mkdir(parents=True)
    os.chmod(d / "00_brutos", 0o700)
    os.chmod(d / "_cofre", 0o700)

    db.inicializar(d / "caso.db", db.DDL_CASO)
    cofre = d / "_cofre" / "identidades.db"
    db.inicializar(cofre, db.DDL_COFRE)
    os.chmod(cofre, 0o600)

    estado = {
        "codinome": codinome,
        "fase": "novo",
        "criado_em": agora(),
        "atualizado_em": None,
        "agentes_concluidos": [],
        "ciclos_revisao": {},
        "pendencias": [],
        "alertas": [],
    }
    _gravar_estado(d, estado)
    return {"codinome": codinome, "caminho": str(d), "brutos": str(d / "00_brutos"), "fase": "novo"}


# ---------- caso status ----------

def _ler_manifesto(d: Path) -> list[dict]:
    arq = d / "01_custodia" / "manifesto.json"
    if not arq.is_file():
        return []
    return json.loads(arq.read_text(encoding="utf-8"))


def status(codinome: str, manifesto: bool = False) -> dict:
    d = caminho(codinome)
    estado = _ler_estado(d)
    entradas = _ler_manifesto(d)
    ingeridos = {e.get("nome") for e in entradas}
    arquivos = sorted(p.name for p in (d / "00_brutos").iterdir() if p.is_file())
    pendentes = [a for a in arquivos if a not in ingeridos]
    produtos = sorted(p.name for p in (d / "04_produtos").iterdir() if p.is_file())

    saida = {
        "codinome": codinome,
        "fase": estado["fase"],
        "criado_em": estado["criado_em"],
        "atualizado_em": estado["atualizado_em"],
        "brutos": {"arquivos": len(arquivos), "ingeridos": len(arquivos) - len(pendentes), "pendentes": len(pendentes)},
        "documentos": len(entradas),
        "agentes_concluidos": estado["agentes_concluidos"],
        "ciclos_revisao": estado["ciclos_revisao"],
        "pendencias": estado["pendencias"],
        "alertas": estado["alertas"],
        "produtos": produtos,
    }
    if manifesto:
        saida["manifesto"] = entradas
    return saida


# ---------- caso estado ----------

def _partir(par: str) -> tuple[str, str]:
    if "=" not in par:
        raise ErroCaso(f"esperado chave=valor, recebido {par!r}")
    chave, valor = par.split("=", 1)
    chave = chave.strip()
    if not chave:
        raise ErroCaso(f"esperado chave=valor, recebido {par!r}")
    if chave.split(".", 1)[0] in CHAVES_PROTEGIDAS:
        raise ErroCaso(f"chave {chave!r} não pode ser alterada")
    return chave, valor


def _interpretar(valor: str):
    try:
        return json.loads(valor)
    except ValueError:
        return valor


def _definir(estado: dict, chave: str, valor) -> None:
    partes = chave.split(".")
    alvo = estado
    for parte in partes[:-1]:
        alvo = alvo.setdefault(parte, {})
        if not isinstance(alvo, dict):
            raise ErroCaso(f"chave {chave!r}: {parte!r} não é um objeto")
    alvo[partes[-1]] = valor


def _acrescentar(estado: dict, chave: str, item) -> None:
    partes = chave.split(".")
    alvo = estado
    for parte in partes[:-1]:
        alvo = alvo.setdefault(parte, {})
    lista = alvo.setdefault(partes[-1], [])
    if not isinstance(lista, list):
        raise ErroCaso(f"chave {chave!r} não é uma lista; use --set")
    if item not in lista:
        lista.append(item)


def estado(codinome: str, sets: list[str] | None = None, adds: list[str] | None = None) -> dict:
    d = caminho(codinome)
    atual = _ler_estado(d)
    if not sets and not adds:
        return atual

    for par in sets or []:
        chave, valor = _partir(par)
        valor = _interpretar(valor)
        if chave == "fase" and valor not in FASES:
            raise ErroCaso(f"fase desconhecida: {valor!r}; use uma de {list(FASES)}")
        _definir(atual, chave, valor)
    for par in adds or []:
        chave, valor = _partir(par)
        _acrescentar(atual, chave, _interpretar(valor))

    _gravar_estado(d, atual)
    return atual
