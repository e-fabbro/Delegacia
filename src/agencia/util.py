"""Utilitários determinísticos compartilhados: valores em centavos, datas ISO, tokens e ponteiros."""
import datetime as dt
import re

RE_TOKEN_PESSOA = re.compile(r"\b(PF|PJ)-\d{4}\b")
RE_TOKEN_CONTA = re.compile(r"\bCT-\d{4}\b")
RE_VALOR_BR = re.compile(r"-?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|-?\d+(?:,\d{1,2})?")

FORMATOS_DATA = ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d.%m.%Y")


def centavos(texto, separador_decimal: str = ",") -> int | None:
    """'R$ 150.000,00' -> 15000000; '-45,90' -> -4590; '' -> None. Nunca usa float."""
    if texto is None:
        return None
    if isinstance(texto, int):
        return texto * 100
    if isinstance(texto, float):
        return int(round(texto * 100))
    s = str(texto).strip().replace("R$", "").replace(" ", "")
    if not s:
        return None
    negativo = s.startswith("-") or s.startswith("(") or s.endswith("-") or s.endswith("D")
    s = s.strip("()-DC")
    if separador_decimal == ",":
        s = s.replace(".", "")
        inteiro, _, frac = s.partition(",")
    else:
        s = s.replace(",", "")
        inteiro, _, frac = s.partition(".")
    if not inteiro.isdigit() and not (inteiro == "" and frac.isdigit()):
        return None
    frac = (frac + "00")[:2]
    if not frac.isdigit():
        return None
    valor = int(inteiro or 0) * 100 + int(frac)
    return -valor if negativo else valor


def reais(centavos_: int | None) -> float | None:
    return None if centavos_ is None else round(centavos_ / 100, 2)


def data_iso(texto, formatos=FORMATOS_DATA) -> str | None:
    """'05/01/2026' -> '2026-01-05'. Datas com hora perdem a hora."""
    if texto is None:
        return None
    if isinstance(texto, dt.datetime):
        return texto.date().isoformat()
    if isinstance(texto, dt.date):
        return texto.isoformat()
    s = str(texto).strip()
    if not s:
        return None
    for f in formatos:
        try:
            return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def data_hora_iso(texto, formatos=FORMATOS_DATA) -> str | None:
    s = str(texto or "").strip()
    for f in formatos:
        try:
            return dt.datetime.strptime(s, f).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def dias_entre(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def ponteiro(doc_id: str, localizador: str) -> str:
    return f"[F:{doc_id}:{localizador}]"


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", texto.lower()).strip("_")
