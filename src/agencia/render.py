"""`render`: reidentifica um produto de 04_produtos/ (fora do alcance do modelo) e gera
.md e .docx em 04_produtos/render/. Conversão Markdown básica: títulos, tabelas, listas, parágrafos.
"""
import re
from pathlib import Path

import docx

from agencia import caso, cofre

RE_TITULO = re.compile(r"^(#{1,6})\s+(.*)$")
RE_LISTA = re.compile(r"^\s*[-*]\s+(.*)$")
RE_SEPARADOR = re.compile(r"^\|?\s*:?-{2,}")


def _limpar(texto: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", texto).strip()


def _celulas(linha: str) -> list[str]:
    return [c.strip() for c in linha.strip().strip("|").split("|")]


def md_para_docx(texto: str, destino: Path) -> None:
    doc = docx.Document()
    linhas = texto.split("\n")
    i = 0
    while i < len(linhas):
        linha = linhas[i]
        if linha.strip().startswith("|"):
            bloco = []
            while i < len(linhas) and linhas[i].strip().startswith("|"):
                if not RE_SEPARADOR.match(linhas[i].strip().strip("|").strip()):
                    bloco.append(_celulas(linhas[i]))
                i += 1
            if bloco:
                ncol = max(len(l) for l in bloco)
                tabela = doc.add_table(rows=len(bloco), cols=ncol)
                tabela.style = "Table Grid"
                for r, l in enumerate(bloco):
                    for c, valor in enumerate(l):
                        tabela.cell(r, c).text = _limpar(valor)
            continue
        m = RE_TITULO.match(linha)
        if m:
            doc.add_heading(_limpar(m.group(2)), level=min(len(m.group(1)), 9))
        elif RE_LISTA.match(linha):
            doc.add_paragraph(_limpar(RE_LISTA.match(linha).group(1)), style="List Bullet")
        elif linha.strip():
            doc.add_paragraph(_limpar(linha))
        i += 1
    doc.save(destino)


def render(codinome: str, arquivo: str) -> dict:
    d = caso.caminho(codinome)
    base = (d / "04_produtos").resolve()
    alvo = (base / arquivo).resolve()
    if base not in alvo.parents or not alvo.is_file():
        raise caso.ErroCaso(f"arquivo {arquivo!r} não encontrado em 04_produtos/")
    cf = cofre.Cofre(d / "_cofre" / "identidades.db")
    texto, subst, restantes = cf.reidentificar_contando(alvo.read_text(encoding="utf-8"))

    pasta = base / "render"
    pasta.mkdir(exist_ok=True)
    md_out = pasta / (alvo.stem + ".md")
    docx_out = pasta / (alvo.stem + ".docx")
    md_out.write_text(texto, encoding="utf-8")
    md_para_docx(texto, docx_out)
    return {
        "codinome": codinome,
        "origem": str(alvo.relative_to(base.parent)),
        "md": str(md_out),
        "docx": str(docx_out),
        "tokens_substituidos": subst,
        "tokens_remanescentes": restantes,
    }
