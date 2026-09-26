"""CLI do pacote agencia: `python -m agencia <grupo> <comando> ...`.

Toda saída é JSON (padrão) ou tabela Markdown (--md), sempre pseudonimizada.
Erros de uso saem em JSON no stderr com código 1.
"""
import argparse

from agencia import caso, cofre, ingestao, render, saida


def _md(p: argparse.ArgumentParser) -> None:
    p.add_argument("--md", action="store_true", help="saída em tabela Markdown")


def construir_parser() -> argparse.ArgumentParser:
    raiz = argparse.ArgumentParser(prog="python -m agencia", description="Agência Nexo — ferramentas determinísticas")
    grupos = raiz.add_subparsers(dest="grupo", required=True)

    # ---- caso ----
    p_caso = grupos.add_parser("caso", help="ciclo de vida do caso")
    sub = p_caso.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("novo", help="cria a estrutura de um caso")
    p.add_argument("codinome")
    _md(p)
    p.set_defaults(fn=lambda a: caso.novo(a.codinome))

    p = sub.add_parser("status", help="andamento do caso (estado.json + contagens)")
    p.add_argument("codinome")
    p.add_argument("--manifesto", action="store_true", help="inclui as entradas do manifesto")
    _md(p)
    p.set_defaults(fn=lambda a: caso.status(a.codinome, manifesto=a.manifesto))

    p = sub.add_parser("estado", help="lê ou altera estado.json")
    p.add_argument("codinome")
    p.add_argument("--set", dest="sets", action="append", metavar="CHAVE=VALOR",
                   help="define chave (valor JSON ou texto; chave pode ser aninhada com ponto)")
    p.add_argument("--add", dest="adds", action="append", metavar="CHAVE=ITEM",
                   help="acrescenta item a uma lista, sem duplicar")
    _md(p)
    p.set_defaults(fn=lambda a: caso.estado(a.codinome, sets=a.sets, adds=a.adds))

    # ---- ingerir ----
    p = grupos.add_parser("ingerir", help="ingere os brutos novos: hash, custódia, classificação, extração, pseudonimização")
    p.add_argument("codinome")
    p.add_argument("--reclassificar", metavar="DOC-ID", help="reclassifica manualmente um documento (exige --tipo)")
    p.add_argument("--tipo", choices=ingestao.TIPOS, help="tipo a atribuir com --reclassificar")
    _md(p)
    p.set_defaults(fn=lambda a: ingestao.ingerir(a.codinome, reclassificar=a.reclassificar, tipo=a.tipo))

    # ---- cofre ----
    p_cofre = grupos.add_parser("cofre", help="varreduras do cofre (nunca expõe valores)")
    sub = p_cofre.add_subparsers(dest="comando", required=True)
    p = sub.add_parser("vazamento", help="conta identificadores em claro nos extraídos (ou em --arquivo)")
    p.add_argument("codinome")
    p.add_argument("--arquivo", metavar="REL", help="arquivo relativo ao caso, ex.: 04_produtos/x.md")
    _md(p)
    p.set_defaults(fn=lambda a: cofre.vazamento(a.codinome, arquivo=a.arquivo))

    # ---- render ----
    p = grupos.add_parser("render", help="reidentifica um produto e gera .md/.docx em 04_produtos/render/")
    p.add_argument("codinome")
    p.add_argument("arquivo", help="relativo a 04_produtos/, ex.: informacao_analise_v1.md")
    _md(p)
    p.set_defaults(fn=lambda a: render.render(a.codinome, a.arquivo))

    return raiz


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        resultado = args.fn(args)
    except caso.ErroCaso as e:
        return saida.erro(str(e))
    saida.emitir(resultado, md=getattr(args, "md", False))
    return 0
