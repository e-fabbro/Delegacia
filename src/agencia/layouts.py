"""Layouts de fontes (config/layouts/*.yaml): mapeiam colunas e padrões de texto para o modelo interno.

O diretório pode ser trocado com AGENCIA_LAYOUTS (útil para testar layouts de um banco específico).
"""
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

from agencia.cofre import sem_acentos

PADRAO = Path(__file__).resolve().parents[2] / "config" / "layouts"


class ErroLayout(Exception):
    pass


def diretorio() -> Path:
    return Path(os.environ.get("AGENCIA_LAYOUTS") or PADRAO)


@lru_cache(maxsize=None)
def _carregar(caminho: str) -> dict:
    p = Path(caminho)
    if not p.is_file():
        raise ErroLayout(f"layout não encontrado: {p}")
    dados = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(dados, dict):
        raise ErroLayout(f"layout inválido (esperado mapeamento): {p}")
    return dados


def carregar(nome: str) -> dict:
    return _carregar(str(diretorio() / f"{nome}.yaml"))


def _norm(coluna: str) -> str:
    return re.sub(r"\s+", "_", sem_acentos(str(coluna)).strip().upper())


def resolver_colunas(mapa: dict[str, list[str]], colunas: list[str]) -> dict[str, int]:
    """{campo: [nomes aceitos]} × cabeçalho real -> {campo: índice}. Usa o primeiro nome que existir."""
    indice = {}
    for i, c in enumerate(colunas):
        indice.setdefault(_norm(c), i)
    saida = {}
    for campo, nomes in mapa.items():
        if isinstance(nomes, str):
            nomes = [nomes]
        for nome in nomes or []:
            if _norm(nome) in indice:
                saida[campo] = indice[_norm(nome)]
                break
    return saida


def compilar(padroes) -> list[re.Pattern]:
    if padroes is None:
        return []
    if isinstance(padroes, str):
        padroes = [padroes]
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in padroes]


def primeiro(padroes, texto: str) -> re.Match | None:
    """Primeiro padrão que casa em `texto` (comparação sem acentos, spans válidos no texto original)."""
    plano = sem_acentos(texto)
    for rx in compilar(padroes):
        m = rx.search(plano)
        if m:
            return m
    return None
