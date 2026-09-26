"""F2 — RIF (COAF): `rif parse/resumo/comunicacoes/envolvidos/sobreposicao`.

Trabalha sobre o extraído pseudonimizado (02_extraido/DOC-###.md); nunca sobre o bruto.
Números: soma bruta de comunicações é apenas referência. O consolidado por titular usa o
"piso sem sobreposição": em cada grupo de comunicações do mesmo titular com períodos sobrepostos,
conta-se apenas o maior valor.
"""
import collections
import json
import re
from pathlib import Path

from agencia import caso, db, layouts, util
from agencia.cofre import sem_acentos

RE_PAGINA = re.compile(r"^<!-- p(\d+) -->\s*$")
LAYOUT = "rif"


def _texto_extraido(d: Path, doc_id: str) -> list[tuple[int, str]]:
    """[(pagina, linha)] do extraído, sem o cabeçalho gerado pela ingestão."""
    arq = d / "02_extraido" / f"{doc_id}.md"
    if not arq.is_file():
        raise caso.ErroCaso(f"extraído {doc_id} não encontrado; rode `ingerir` antes")
    pagina, saida = 1, []
    for linha in arq.read_text(encoding="utf-8").split("\n"):
        m = RE_PAGINA.match(linha)
        if m:
            pagina = int(m.group(1))
            continue
        if linha.startswith("# DOC-") or linha.startswith("tipo: "):
            continue
        saida.append((pagina, linha))
    return saida


def _grupo(padroes, texto: str, n: int = 1) -> str | None:
    m = layouts.primeiro(padroes, texto)
    if not m or m.lastindex is None or m.lastindex < n:
        return None
    ini, fim = m.span(n)
    return texto[ini:fim].strip() or None


def _periodo(padroes, texto: str) -> tuple[str | None, str | None]:
    m = layouts.primeiro(padroes, texto)
    if not m:
        return None, None
    ini = util.data_iso(texto[m.start(1):m.end(1)])
    fim = util.data_iso(texto[m.start(2):m.end(2)]) if m.lastindex and m.lastindex >= 2 and m.group(2) else ini
    return ini, fim or ini


def _tipo_origem(lay: dict, origem: str | None) -> str:
    if not origem:
        return "nao_identificada"
    plano = sem_acentos(origem).lower()
    for tipo, pistas in (lay.get("tipo_origem") or {}).items():
        if any(p.lower() in plano for p in pistas):
            return tipo
    return "nao_identificada"


def _papel(lay: dict, linha: str) -> str:
    for papel, padroes in (lay["comunicacao"].get("papeis") or {}).items():
        if layouts.primeiro(padroes, linha):
            return papel
    return "envolvido"


def _envolvidos(lay: dict, linhas: list[str]) -> list[dict]:
    vistos, saida = set(), []
    for linha in linhas:
        papel = _papel(lay, linha)
        for m in util.RE_TOKEN_PESSOA.finditer(linha):
            chave = (m.group(0), papel)
            if chave not in vistos:
                vistos.add(chave)
                saida.append({"pseudonimo": m.group(0), "papel": papel})
    return saida


def _blocos(lay: dict, linhas: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], list[dict]]:
    """Separa cabeçalho e blocos de comunicação: [{num, pagina, linhas}]."""
    inicio = lay["comunicacao"]["inicio"]
    cabecalho, blocos, atual = [], [], None
    for pagina, linha in linhas:
        m = layouts.primeiro(inicio, linha)
        if m:
            atual = {"num": int(m.group(1)), "pagina": pagina, "linhas": [linha]}
            blocos.append(atual)
        elif atual is None:
            cabecalho.append((pagina, linha))
        else:
            atual["linhas"].append(linha)
    return cabecalho, blocos


def parse(codinome: str, doc_id: str, forcar: bool = False) -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    doc = con.execute("select tipo, paginas from documentos where doc_id=?", (doc_id,)).fetchone()
    if doc is None:
        raise caso.ErroCaso(f"{doc_id} não consta de caso.db; rode `ingerir`")
    if doc["tipo"] != "RIF" and not forcar:
        raise caso.ErroCaso(f"{doc_id} está classificado como {doc['tipo']}, não RIF (use --forcar ou reclassifique)")

    lay = layouts.carregar(LAYOUT)
    linhas = _texto_extraido(d, doc_id)
    cab_linhas, blocos = _blocos(lay, linhas)
    cab_texto = "\n".join(l for _, l in cab_linhas)
    lc = lay["cabecalho"]
    origem = _grupo(lc.get("origem"), cab_texto)
    p_ini, p_fim = _periodo(lc.get("periodo"), cab_texto)
    total_inf = _grupo(lc.get("total_comunicacoes"), cab_texto)
    cabecalho = {
        "doc_id": doc_id,
        "numero": _grupo(lc.get("numero"), cab_texto),
        "data": util.data_iso(_grupo(lc.get("data"), cab_texto)),
        "destinatario": _grupo(lc.get("destinatario"), cab_texto),
        "origem": origem,
        "tipo_origem": _tipo_origem(lay, origem),
        "pedido": _grupo(lc.get("pedido"), origem or cab_texto),
        "periodo_inicio": p_ini,
        "periodo_fim": p_fim,
        "total_informado": int(total_inf) if total_inf and total_inf.isdigit() else None,
        "paginas": doc["paginas"],
    }

    lcm = lay["comunicacao"]
    comunicacoes, avisos = [], []
    for b in blocos:
        texto = "\n".join(b["linhas"])
        ini, fim = _periodo(lcm.get("periodo"), texto)
        valor = util.centavos(_grupo(lcm.get("valor"), texto))
        envolvidos = _envolvidos(lay, b["linhas"])
        titular = next((e["pseudonimo"] for e in envolvidos if e["papel"] == "titular"), None)
        c = {
            "doc_id": doc_id, "num": b["num"], "pagina": b["pagina"],
            "tipo": (_grupo(lcm.get("tipo"), texto) or "").upper() or None,
            "comunicante": _grupo(lcm.get("comunicante"), texto),
            "segmento": _grupo(lcm.get("segmento"), texto),
            "data_comunicacao": util.data_iso(_grupo(lcm.get("data_comunicacao"), texto)),
            "periodo_inicio": ini, "periodo_fim": fim,
            "valor_centavos": valor,
            "titular": titular,
            "envolvidos": [e for e in envolvidos if e["papel"] != "comunicante"],
            "enquadramento": _grupo(lcm.get("enquadramento"), texto),
            "informacoes": _grupo(lcm.get("informacoes"), texto),
            "texto": texto,
        }
        for campo in ("tipo", "comunicante", "periodo_inicio", "valor_centavos", "titular"):
            if c[campo] is None:
                avisos.append(f"com#{b['num']}: campo {campo} não identificado")
        comunicacoes.append(c)

    nums = [c["num"] for c in comunicacoes]
    if len(nums) != len(set(nums)):
        avisos.append("numeração de comunicações repetida; verifique o layout")
    if cabecalho["total_informado"] is not None and cabecalho["total_informado"] != len(comunicacoes):
        avisos.append(f"total informado no cabeçalho ({cabecalho['total_informado']}) difere do extraído ({len(comunicacoes)})")

    con.execute("delete from comunicacoes_rif where doc_id=?", (doc_id,))
    con.execute("delete from rif_cabecalho where doc_id=?", (doc_id,))
    con.execute(
        "insert into rif_cabecalho values (:doc_id, :numero, :data, :destinatario, :origem, :tipo_origem, :pedido,"
        " :periodo_inicio, :periodo_fim, :total_informado, :paginas, :parseado_em)",
        {**cabecalho, "parseado_em": caso.agora()},
    )
    con.executemany(
        "insert into comunicacoes_rif values (:doc_id, :num, :pagina, :tipo, :comunicante, :segmento, :data_comunicacao,"
        " :periodo_inicio, :periodo_fim, :valor_centavos, :titular, :envolvidos, :enquadramento, :informacoes, :texto)",
        [{**c, "envolvidos": json.dumps(c["envolvidos"], ensure_ascii=False)} for c in comunicacoes],
    )
    con.commit()
    con.close()

    tokens = {e["pseudonimo"] for c in comunicacoes for e in c["envolvidos"]}
    return {
        "codinome": codinome,
        "doc_id": doc_id,
        "cabecalho": cabecalho,
        "comunicacoes": len(comunicacoes),
        "por_tipo": dict(collections.Counter(c["tipo"] or "?" for c in comunicacoes)),
        "envolvidos_distintos": len(tokens),
        "soma_bruta_nao_consolidada": util.reais(sum(c["valor_centavos"] or 0 for c in comunicacoes)),
        "avisos": avisos,
    }


# ---------- consultas ----------

def _con(codinome: str):
    return db.abrir_caso(caso.caminho(codinome))


def _carregar(con, doc: str | None = None) -> list[dict]:
    sql = "select * from comunicacoes_rif"
    args: tuple = ()
    if doc:
        sql += " where doc_id=?"
        args = (doc,)
    linhas = [dict(l) for l in con.execute(sql + " order by doc_id, num", args)]
    for l in linhas:
        l["envolvidos"] = json.loads(l["envolvidos"])
        l["valor"] = util.reais(l.pop("valor_centavos"))
        l["ponteiro"] = util.ponteiro(l["doc_id"], f"com#{l['num']}")
    if not linhas:
        raise caso.ErroCaso("nenhuma comunicação em caso.db; rode `rif parse <COD> DOC-###`")
    return linhas


def _grupos_sobreposicao(comms: list[dict]) -> list[list[dict]]:
    """Agrupa comunicações do mesmo titular cujos períodos se sobrepõem (fecho transitivo)."""
    grupos = []
    por_titular = collections.defaultdict(list)
    for c in comms:
        if c["titular"] and c["periodo_inicio"]:
            por_titular[c["titular"]].append(c)
    for titular, lista in por_titular.items():
        lista.sort(key=lambda c: (c["periodo_inicio"], c["periodo_fim"] or c["periodo_inicio"]))
        atual, fim_atual = [], None
        for c in lista:
            fim_c = c["periodo_fim"] or c["periodo_inicio"]
            if atual and c["periodo_inicio"] <= fim_atual:
                atual.append(c)
                fim_atual = max(fim_atual, fim_c)
            else:
                if atual:
                    grupos.append(atual)
                atual, fim_atual = [c], fim_c
        if atual:
            grupos.append(atual)
    return grupos


def sobreposicao(codinome: str, doc: str | None = None) -> dict:
    con = _con(codinome)
    comms = _carregar(con, doc)
    con.close()
    grupos, pares, por_titular = [], [], {}
    for g in _grupos_sobreposicao(comms):
        titular = g[0]["titular"]
        bruta = sum(c["valor"] or 0 for c in g)
        piso = max((c["valor"] or 0 for c in g), default=0)
        t = por_titular.setdefault(titular, {"titular": titular, "comunicacoes": 0, "grupos_sobrepostos": 0, "soma_bruta_centavos": 0, "piso_centavos": 0})
        t["comunicacoes"] += len(g)
        t["soma_bruta_centavos"] += round(bruta * 100)
        t["piso_centavos"] += round(piso * 100)
        if len(g) > 1:
            t["grupos_sobrepostos"] += 1
            grupos.append({
                "titular": titular,
                "comunicacoes": [c["ponteiro"] for c in g],
                "periodo_inicio": min(c["periodo_inicio"] for c in g),
                "periodo_fim": max(c["periodo_fim"] or c["periodo_inicio"] for c in g),
                "soma_bruta": round(bruta, 2),
                "piso_sem_sobreposicao": round(piso, 2),
                "comunicantes": sorted({c["comunicante"] or "?" for c in g}),
            })
            for i, a in enumerate(g):
                for b in g[i + 1:]:
                    fim_a, fim_b = a["periodo_fim"] or a["periodo_inicio"], b["periodo_fim"] or b["periodo_inicio"]
                    if b["periodo_inicio"] <= fim_a and a["periodo_inicio"] <= fim_b:
                        pares.append({
                            "titular": titular, "a": a["ponteiro"], "b": b["ponteiro"],
                            "sobreposicao_inicio": max(a["periodo_inicio"], b["periodo_inicio"]),
                            "sobreposicao_fim": min(fim_a, fim_b),
                            "mesmo_comunicante": a["comunicante"] == b["comunicante"],
                        })
    consolidado = [
        {"titular": t["titular"], "comunicacoes": t["comunicacoes"], "grupos_sobrepostos": t["grupos_sobrepostos"],
         "soma_bruta": util.reais(t["soma_bruta_centavos"]), "piso_sem_sobreposicao": util.reais(t["piso_centavos"])}
        for t in sorted(por_titular.values(), key=lambda t: -t["piso_centavos"])
    ]
    return {
        "codinome": codinome,
        "comunicacoes_analisadas": len(comms),
        "comunicacoes_sem_titular_ou_periodo": sum(1 for c in comms if not (c["titular"] and c["periodo_inicio"])),
        "pares_sobrepostos": len(pares),
        "pares": pares,
        "grupos": grupos,
        "por_titular": consolidado,
        "nota": "piso_sem_sobreposicao = soma, por titular, do maior valor de cada grupo de comunicações com períodos sobrepostos; soma_bruta é só referência.",
    }


def envolvidos(codinome: str, doc: str | None = None) -> dict:
    con = _con(codinome)
    comms = _carregar(con, doc)
    con.close()
    piso = {t["titular"]: t for t in sobreposicao(codinome, doc)["por_titular"]}
    por_token: dict[str, dict] = {}
    for c in comms:
        for e in c["envolvidos"]:
            t = por_token.setdefault(e["pseudonimo"], {
                "pseudonimo": e["pseudonimo"], "n_comunicacoes": 0, "papeis": set(), "comunicantes": set(),
                "tipos": set(), "periodo_inicio": None, "periodo_fim": None, "comunicacoes": [], "_vistas": set(),
            })
            t["papeis"].add(e["papel"])
            if c["comunicante"]:
                t["comunicantes"].add(c["comunicante"])
            if c["tipo"]:
                t["tipos"].add(c["tipo"])
            if c["ponteiro"] not in t["_vistas"]:
                t["_vistas"].add(c["ponteiro"])
                t["n_comunicacoes"] += 1
                t["comunicacoes"].append(c["ponteiro"])
            if c["periodo_inicio"]:
                t["periodo_inicio"] = min(filter(None, [t["periodo_inicio"], c["periodo_inicio"]]))
                t["periodo_fim"] = max(filter(None, [t["periodo_fim"], c["periodo_fim"] or c["periodo_inicio"]]))
    saida = []
    for t in por_token.values():
        p = piso.get(t["pseudonimo"])
        saida.append({
            "pseudonimo": t["pseudonimo"],
            "n_comunicacoes": t["n_comunicacoes"],
            "papeis": sorted(t["papeis"]),
            "tipos": sorted(t["tipos"]),
            "comunicantes": sorted(t["comunicantes"]),
            "periodo_inicio": t["periodo_inicio"],
            "periodo_fim": t["periodo_fim"],
            "como_titular": {
                "comunicacoes": p["comunicacoes"] if p else 0,
                "soma_bruta": p["soma_bruta"] if p else 0.0,
                "piso_sem_sobreposicao": p["piso_sem_sobreposicao"] if p else 0.0,
                "grupos_sobrepostos": p["grupos_sobrepostos"] if p else 0,
            },
            "comunicacoes": t["comunicacoes"],
        })
    saida.sort(key=lambda t: (-t["n_comunicacoes"], -t["como_titular"]["piso_sem_sobreposicao"], t["pseudonimo"]))
    return {"codinome": codinome, "envolvidos": len(saida), "lista": saida}


def comunicacoes(codinome: str, doc: str | None = None, envolvido: str | None = None, tipo: str | None = None,
                 limite: int | None = None) -> dict:
    con = _con(codinome)
    comms = _carregar(con, doc)
    con.close()
    if envolvido:
        comms = [c for c in comms if any(e["pseudonimo"] == envolvido for e in c["envolvidos"])]
    if tipo:
        comms = [c for c in comms if (c["tipo"] or "").upper() == tipo.upper()]
    total = len(comms)
    if limite:
        comms = comms[:limite]
    lista = [
        {k: c[k] for k in ("ponteiro", "doc_id", "num", "pagina", "tipo", "comunicante", "segmento", "data_comunicacao",
                           "periodo_inicio", "periodo_fim", "valor", "titular", "enquadramento", "informacoes")}
        | {"envolvidos": ", ".join(f"{e['pseudonimo']}:{e['papel']}" for e in c["envolvidos"])}
        for c in comms
    ]
    return {"codinome": codinome, "total": total, "exibidas": len(lista), "comunicacoes": lista}


def resumo(codinome: str, doc: str | None = None) -> dict:
    con = _con(codinome)
    cabs = [dict(l) for l in con.execute("select * from rif_cabecalho" + (" where doc_id=?" if doc else ""), (doc,) if doc else ())]
    comms = _carregar(con, doc)
    con.close()
    if not cabs:
        raise caso.ErroCaso("nenhum RIF parseado; rode `rif parse <COD> DOC-###`")
    sob = sobreposicao(codinome, doc)
    por_com = collections.Counter(c["comunicante"] or "?" for c in comms)
    return {
        "codinome": codinome,
        "rifs": cabs,
        "comunicacoes": len(comms),
        "por_tipo": dict(collections.Counter(c["tipo"] or "?" for c in comms)),
        "por_comunicante": [{"comunicante": k, "comunicacoes": v} for k, v in por_com.most_common()],
        "por_segmento": dict(collections.Counter(c["segmento"] or "?" for c in comms)),
        "titulares_distintos": len({c["titular"] for c in comms if c["titular"]}),
        "envolvidos_distintos": len({e["pseudonimo"] for c in comms for e in c["envolvidos"]}),
        "periodo_inicio": min((c["periodo_inicio"] for c in comms if c["periodo_inicio"]), default=None),
        "periodo_fim": max((c["periodo_fim"] for c in comms if c["periodo_fim"]), default=None),
        "soma_bruta_nao_consolidada": util.reais(sum(round((c["valor"] or 0) * 100) for c in comms)),
        "piso_sem_sobreposicao": util.reais(sum(round(t["piso_sem_sobreposicao"] * 100) for t in sob["por_titular"])),
        "pares_sobrepostos": sob["pares_sobrepostos"],
    }
