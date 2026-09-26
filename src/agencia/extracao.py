"""Extração de texto e tabelas de brutos: PDF, XLSX, CSV, TXT. Não altera o bruto."""
import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pdfplumber

FORMATOS = {".pdf": "pdf", ".xlsx": "xlsx", ".xlsm": "xlsx", ".csv": "csv", ".txt": "txt", ".md": "txt"}


class NaoSuportado(Exception):
    pass


@dataclass
class Tabela:
    nome: str
    colunas: list[str]
    linhas: list[list[str]]


@dataclass
class Extraido:
    formato: str
    paginas: list[str] | None = None   # pdf
    linhas: list[str] | None = None    # txt
    tabelas: list[Tabela] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def texto(self) -> str:
        partes = list(self.paginas or []) + list(self.linhas or [])
        for t in self.tabelas:
            partes.append(";".join(t.colunas))
            partes.extend(";".join(l) for l in t.linhas[:50])
        return "\n".join(partes)


def _cel(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.strftime("%d/%m/%Y %H:%M:%S") if (v.hour or v.minute or v.second) else v.strftime("%d/%m/%Y")
    if isinstance(v, dt.date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _nome_seguro(nome: str) -> str:
    return re.sub(r"[^\w.-]+", "_", nome).strip("_") or "tabela"


def _pdf(caminho: Path) -> Extraido:
    ext = Extraido(formato="pdf", paginas=[])
    with pdfplumber.open(caminho) as pdf:
        for n, pagina in enumerate(pdf.pages, start=1):
            try:
                texto = pagina.extract_text() or ""
            except Exception as e:  # pdfplumber é tolerante, mas PDFs corrompidos existem
                texto = ""
                ext.avisos.append(f"p{n}: falha na extração de texto ({e})")
            if not texto.strip():
                ext.avisos.append(f"p{n}: sem texto extraível (imagem? exige OCR)")
            ext.paginas.append(texto)
            try:
                tabelas = pagina.extract_tables() or []
            except Exception:
                tabelas = []
            for k, t in enumerate(tabelas, start=1):
                if t and len(t) > 1:
                    ext.tabelas.append(Tabela(f"p{n}_t{k}", [_cel(c) for c in t[0]], [[_cel(c) for c in l] for l in t[1:]]))
    return ext


def _xlsx(caminho: Path) -> Extraido:
    ext = Extraido(formato="xlsx")
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    for ws in wb.worksheets:
        linhas = [list(l) for l in ws.iter_rows(values_only=True)]
        linhas = [l for l in linhas if any(c is not None and str(c).strip() for c in l)]
        if not linhas:
            ext.avisos.append(f"planilha {ws.title!r} vazia")
            continue
        colunas = [_cel(c) for c in linhas[0]]
        while colunas and not colunas[-1]:
            colunas.pop()
        corpo = [[_cel(c) for c in l[:len(colunas)]] + [""] * max(0, len(colunas) - len(l)) for l in linhas[1:]]
        ext.tabelas.append(Tabela(_nome_seguro(ws.title), colunas, corpo))
    wb.close()
    return ext


def _decodificar(caminho: Path) -> str:
    dados = caminho.read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return dados.decode(enc)
        except UnicodeDecodeError:
            continue
    return dados.decode("utf-8", errors="replace")


def _csv(caminho: Path) -> Extraido:
    ext = Extraido(formato="csv")
    texto = _decodificar(caminho)
    primeira = texto.splitlines()[0] if texto.strip() else ""
    delim = max(";,\t|", key=primeira.count) if primeira else ";"
    leitor = csv.reader(io.StringIO(texto), delimiter=delim)
    linhas = [l for l in leitor if any(c.strip() for c in l)]
    if not linhas:
        ext.avisos.append("csv vazio")
        return ext
    colunas = [c.strip() for c in linhas[0]]
    corpo = [[c.strip() for c in l[:len(colunas)]] + [""] * max(0, len(colunas) - len(l)) for l in linhas[1:]]
    ext.tabelas.append(Tabela("dados", colunas, corpo))
    return ext


def _txt(caminho: Path) -> Extraido:
    return Extraido(formato="txt", linhas=_decodificar(caminho).splitlines())


def extrair(caminho: Path) -> Extraido:
    formato = FORMATOS.get(caminho.suffix.lower())
    if formato is None:
        raise NaoSuportado(f"formato não suportado: {caminho.suffix or '(sem extensão)'}")
    return {"pdf": _pdf, "xlsx": _xlsx, "csv": _csv, "txt": _txt}[formato](caminho)
