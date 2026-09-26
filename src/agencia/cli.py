"""CLI do pacote agencia: `python -m agencia <grupo> <comando> ...`.

Toda saída é JSON (padrão) ou tabela Markdown (--md), sempre pseudonimizada.
Erros de uso saem em JSON no stderr com código 1.
"""
import argparse

from agencia import achados, agregados, banco, caso, cofre, grafo, handoff, ingestao, integracao, layouts, matrizes, render, rif, saida


def _md(p: argparse.ArgumentParser) -> None:
    p.add_argument("--md", action="store_true", help="saída em tabela Markdown")


def _filtros(p: argparse.ArgumentParser, conta: bool = True) -> None:
    if conta:
        p.add_argument("--conta", metavar="CT-####", help="restringe a uma conta")
    p.add_argument("--inicio", metavar="AAAA-MM-DD")
    p.add_argument("--fim", metavar="AAAA-MM-DD")
    p.add_argument("--doc", metavar="DOC-ID", help="restringe a um documento")
    p.add_argument("--salvar", action="store_true", help="grava o resultado em 03_analises/_agg/ e devolve ponteiros agg#")
    _md(p)


def _banco(fn, comando: str, **params):
    """Executa uma análise bancária e, com --salvar, registra o agregado (fonte citável)."""
    def executar(a):
        filtros = {k: getattr(a, k) for k in ("conta", "inicio", "fim", "doc") if hasattr(a, k)}
        extras = {k: getattr(a, v) for k, v in params.items()}
        resultado = fn(a.codinome, **extras, **filtros)
        if getattr(a, "salvar", False):
            resultado["salvo"] = agregados.salvar(a.codinome, f"banco_{comando}", {**extras, **{k: v for k, v in filtros.items() if v}}, resultado)
        return resultado
    return executar


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

    # ---- rif (F2) ----
    p_rif = grupos.add_parser("rif", help="Relatório de Inteligência Financeira (COAF)")
    sub = p_rif.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("parse", help="extrai cabeçalho e comunicações do extraído para caso.db")
    p.add_argument("codinome")
    p.add_argument("doc_id")
    p.add_argument("--forcar", action="store_true", help="parseia mesmo se o tipo não for RIF")
    _md(p)
    p.set_defaults(fn=lambda a: rif.parse(a.codinome, a.doc_id, forcar=a.forcar))

    p = sub.add_parser("resumo", help="cabeçalho(s), contagens por tipo/comunicante, soma bruta e piso consolidado")
    p.add_argument("codinome")
    p.add_argument("doc_id", nargs="?")
    _md(p)
    p.set_defaults(fn=lambda a: rif.resumo(a.codinome, a.doc_id))

    p = sub.add_parser("comunicacoes", help="lista as comunicações (filtros: --doc, --envolvido, --tipo, --limite)")
    p.add_argument("codinome")
    p.add_argument("--doc", metavar="DOC-ID")
    p.add_argument("--envolvido", metavar="PF-####|PJ-####")
    p.add_argument("--tipo", choices=["COS", "COA"])
    p.add_argument("--limite", type=int)
    _md(p)
    p.set_defaults(fn=lambda a: rif.comunicacoes(a.codinome, doc=a.doc, envolvido=a.envolvido, tipo=a.tipo, limite=a.limite))

    p = sub.add_parser("envolvidos", help="consolidado por envolvido (sem somar comunicações sobrepostas)")
    p.add_argument("codinome")
    p.add_argument("--doc", metavar="DOC-ID")
    _md(p)
    p.set_defaults(fn=lambda a: rif.envolvidos(a.codinome, doc=a.doc))

    p = sub.add_parser("sobreposicao", help="comunicações do mesmo titular com períodos sobrepostos")
    p.add_argument("codinome")
    p.add_argument("--doc", metavar="DOC-ID")
    _md(p)
    p.set_defaults(fn=lambda a: rif.sobreposicao(a.codinome, doc=a.doc))

    # ---- banco (F3) ----
    p_banco = grupos.add_parser("banco", help="dados bancários (SIMBA, CCS, extratos)")
    sub = p_banco.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("importar", help="importa as tabelas extraídas de um documento (layout em config/layouts/)")
    p.add_argument("codinome")
    p.add_argument("doc_id")
    p.add_argument("--layout", help="força um layout (simba, ccs)")
    _md(p)
    p.set_defaults(fn=lambda a: banco.importar(a.codinome, a.doc_id, layout=a.layout))

    p = sub.add_parser("lancamentos", help="lista lançamentos com filtros")
    p.add_argument("codinome")
    p.add_argument("--contraparte", metavar="PF-####|PJ-####|CT-####")
    p.add_argument("--limite", type=int, default=50)
    _filtros(p)
    p.set_defaults(fn=_banco(banco.lancamentos, "lancamentos", contraparte="contraparte", limite="limite"))

    p = sub.add_parser("integridade", help="lacunas, saldo reconstruído, OD vazio, duplicidades, contas CCS sem extrato")
    p.add_argument("codinome")
    p.add_argument("--lacuna-dias", dest="lacuna_dias", type=int, default=30)
    p.add_argument("--conta", metavar="CT-####")
    p.add_argument("--doc", metavar="DOC-ID")
    p.add_argument("--salvar", action="store_true")
    _md(p)
    p.set_defaults(fn=_banco(banco.integridade, "integridade", lacuna_dias="lacuna_dias"))

    p = sub.add_parser("resumo", help="por conta: período, lançamentos, créditos, débitos, maiores operações")
    p.add_argument("codinome")
    _filtros(p)
    p.set_defaults(fn=_banco(banco.resumo, "resumo"))

    p = sub.add_parser("contrapartes", help="por conta, contrapartes ranqueadas por volume")
    p.add_argument("codinome")
    p.add_argument("--top", type=int, default=20)
    _filtros(p)
    p.set_defaults(fn=_banco(banco.contrapartes, "contrapartes", top="top"))

    p = sub.add_parser("especie", help="depósitos e saques em espécie, por conta e local")
    p.add_argument("codinome")
    _filtros(p)
    p.set_defaults(fn=_banco(banco.especie, "especie"))

    p = sub.add_parser("fracionamento", help="operações abaixo do limiar que, juntas na janela, o ultrapassam")
    p.add_argument("codinome")
    p.add_argument("--limiar", type=float, default=10000.0, help="em reais (padrão 10000)")
    p.add_argument("--janela", choices=["dia", "semana"], default="dia")
    p.add_argument("--minimo", type=int, default=2, help="mínimo de operações no grupo")
    _filtros(p)
    p.set_defaults(fn=_banco(banco.fracionamento, "fracionamento", limiar="limiar", janela="janela", minimo="minimo"))

    p = sub.add_parser("passagem", help="índice de passagem (créditos que saem em até N horas) e giro")
    p.add_argument("codinome")
    p.add_argument("--horas", type=int, default=48)
    _filtros(p)
    p.set_defaults(fn=_banco(banco.passagem, "passagem", horas="horas"))

    p = sub.add_parser("circularidade", help="ciclos de transferência A→B→…→A entre contas/entidades do caso")
    p.add_argument("codinome")
    p.add_argument("--nivel", choices=["conta", "entidade"], default="conta")
    p.add_argument("--max-comprimento", dest="max_comprimento", type=int, default=6)
    p.add_argument("--incluir-terceiros", dest="incluir_terceiros", action="store_true",
                   help="permite contrapartes sem extrato como nós intermediários")
    _filtros(p, conta=False)
    p.set_defaults(fn=_banco(banco.circularidade, "circularidade", nivel="nivel", max_comprimento="max_comprimento",
                             incluir_terceiros="incluir_terceiros"))

    p = sub.add_parser("cruzar-alvos", help="transferências diretas entre alvos do caso (e envolvidos do RIF com --fonte rif)")
    p.add_argument("codinome")
    p.add_argument("--fonte", choices=["rif"])
    _filtros(p, conta=False)
    p.set_defaults(fn=_banco(banco.cruzar_alvos, "cruzar_alvos", fonte="fonte"))

    p = sub.add_parser("linha-tempo", help="movimentação por período com picos sinalizados")
    p.add_argument("codinome")
    p.add_argument("--granularidade", choices=["dia", "semana", "mes"], default="mes")
    _filtros(p)
    p.set_defaults(fn=_banco(banco.linha_tempo, "linha_tempo", granularidade="granularidade"))

    # ---- grafo (F4) ----
    p_grafo = grupos.add_parser("grafo", help="grafo de vínculos")
    sub = p_grafo.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("construir", help="deriva a tabela vinculos de transacoes, comunicacoes_rif, relacionamentos_ccs e achados")
    p.add_argument("codinome")
    p.add_argument("--sem-achados", dest="sem_achados", action="store_true", help="ignora 03_analises/*/achados.jsonl")
    _md(p)
    p.set_defaults(fn=lambda a: grafo.construir(a.codinome, sem_achados=a.sem_achados))

    p = sub.add_parser("centrais", help="grau, intermediação, pontes, componentes, convergência de fontes")
    p.add_argument("codinome")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--tipo", choices=grafo.TIPOS_ARESTA, help="só arestas deste tipo")
    _md(p)
    p.set_defaults(fn=lambda a: grafo.centrais(a.codinome, top=a.top, tipo=a.tipo))

    p = sub.add_parser("exportar", help="04_produtos/grafo.html (autocontido), grafo.json ou grafo.graphml")
    p.add_argument("codinome")
    p.add_argument("--formato", choices=["html", "json", "graphml", "todos"], default="html")
    _md(p)
    p.set_defaults(fn=lambda a: grafo.exportar(a.codinome, formato=a.formato))

    # ---- linha-tempo (F4) ----
    p_lt = grupos.add_parser("linha-tempo", help="linha do tempo")
    sub = p_lt.add_subparsers(dest="comando", required=True)
    p = sub.add_parser("integrada", help="eventos de todas as fontes por período, com marcos e picos")
    p.add_argument("codinome")
    p.add_argument("--granularidade", choices=["dia", "semana", "mes"], default="mes")
    p.add_argument("--entidade", metavar="PF-####|PJ-####|CT-####", help="só eventos que envolvem a entidade")
    p.add_argument("--inicio", metavar="AAAA-MM-DD")
    p.add_argument("--fim", metavar="AAAA-MM-DD")
    _md(p)
    p.set_defaults(fn=lambda a: integracao.integrada(a.codinome, granularidade=a.granularidade, entidade=a.entidade, inicio=a.inicio, fim=a.fim))

    # ---- achados (F4) ----
    p_ach = grupos.add_parser("achados", help="qualidade dos achados e produtos")
    sub = p_ach.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("validar", help="schema, ids únicos, agente e doc_ids de 03_analises/<agente>/achados.jsonl")
    p.add_argument("codinome")
    p.add_argument("agente", nargs="?", help="pasta em 03_analises/ (padrão: todas)")
    _md(p)
    p.set_defaults(fn=lambda a: achados.validar(a.codinome, a.agente))

    p = sub.add_parser("verificar", help="ponteiros resolvem, valores e entidades conferem com caso.db; --arquivo audita ancoragem de um .md")
    p.add_argument("codinome")
    p.add_argument("agente", nargs="?")
    p.add_argument("--arquivo", metavar="REL", help="ex.: 03_analises/analista-rif/nota.md ou 04_produtos/informacao_analise_v1.md")
    _md(p)
    p.set_defaults(fn=lambda a: achados.verificar(a.codinome, a.agente, arquivo=a.arquivo))

    p = sub.add_parser("diligencias", help="consolida as diligências sugeridas nos achados, por tipo, sem duplicatas")
    p.add_argument("codinome")
    p.add_argument("agente", nargs="?")
    _md(p)
    p.set_defaults(fn=lambda a: achados.diligencias(a.codinome, a.agente))

    # ---- saídas (F5) ----
    p = grupos.add_parser("matrizes", help="consolida tabelas do caso e das análises em 04_produtos/matrizes.xlsx (pseudonimizado)")
    p.add_argument("codinome")
    _md(p)
    p.set_defaults(fn=lambda a: matrizes.matrizes(a.codinome))

    p = grupos.add_parser("render", help="reidentifica um produto de 04_produtos/ (md→md+docx, xlsx, html, json) em 04_produtos/render/")
    p.add_argument("codinome")
    p.add_argument("arquivo", help="relativo a 04_produtos/, ex.: informacao_analise_v1.md, matrizes.xlsx, grafo.html")
    p.add_argument("--ponteiros", choices=["legivel", "manter", "remover"], default="legivel",
                   help="[F:DOC-003:tx#2] → [DOC-003, lançamento 2] (legivel, padrão), inalterado (manter) ou removido")
    p.add_argument("--sem-docx", dest="sem_docx", action="store_true", help="para .md, gera só o .md reidentificado")
    _md(p)
    p.set_defaults(fn=lambda a: render.render(a.codinome, a.arquivo, ponteiros=a.ponteiros, docx_=not a.sem_docx))

    p = grupos.add_parser("handoff", help="gera 04_produtos/handoff.json (pseudonimizado) para os pipelines de representação e relatório final")
    p.add_argument("codinome")
    p.add_argument("--reidentificar", action="store_true", help="grava também 04_produtos/render/handoff.json com identidades reais")
    _md(p)
    p.set_defaults(fn=lambda a: handoff.handoff(a.codinome, reidentificar=a.reidentificar))

    return raiz


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        resultado = args.fn(args)
    except (caso.ErroCaso, layouts.ErroLayout) as e:
        return saida.erro(str(e))
    saida.emitir(resultado, md=getattr(args, "md", False))
    return 0
