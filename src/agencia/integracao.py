"""F4 — `linha-tempo integrada`: eventos de todas as fontes de caso.db numa só série temporal.

Fontes: transacoes (bancário), comunicacoes_rif (período comunicado e data da comunicação),
relacionamentos_ccs (início/fim de relacionamento), eventos_telematicos (F6, se existir) e achados com período.
Saída: série por período (contagens e valores por fonte), marcos (eventos pontuais relevantes) e picos.
"""
import collections
import datetime as dt
import json
import statistics

from agencia import caso, db, util
from agencia.grafo import _achados


def _periodo(data: str, granularidade: str) -> str:
    if granularidade == "dia":
        return data
    if granularidade == "semana":
        c = dt.date.fromisoformat(data).isocalendar()
        return f"{c.year}-W{c.week:02d}"
    return data[:7]


def _eventos(con, d, entidade: str | None) -> list[dict]:
    tit = {l["conta"]: l["titular"] for l in con.execute("select conta, titular from contas")}
    ev = []
    for t in con.execute("select * from transacoes"):
        ents = {t["conta"], tit.get(t["conta"]), t["contraparte"], t["contraparte_conta"], tit.get(t["contraparte_conta"])} - {None}
        ev.append({"data": t["data"], "fonte": "bancario", "tipo": f"lancamento_{t['natureza']}", "valor": t["valor_centavos"],
                   "entidades": ents, "ponteiro": util.ponteiro(t["doc_id"], f"tx#{t['tx_id']}"), "descricao": t["historico"]})
    for c in con.execute("select * from comunicacoes_rif"):
        ents = {e["pseudonimo"] for e in json.loads(c["envolvidos"])} | ({c["titular"]} - {None})
        ptr = util.ponteiro(c["doc_id"], f"com#{c['num']}")
        if c["periodo_inicio"]:
            ev.append({"data": c["periodo_inicio"], "fonte": "rif", "tipo": "inicio_periodo_comunicado", "valor": c["valor_centavos"] or 0,
                       "entidades": ents, "ponteiro": ptr, "descricao": f"{c['tipo'] or ''} {c['comunicante'] or ''} até {c['periodo_fim']}".strip(), "marco": True})
        if c["data_comunicacao"]:
            ev.append({"data": c["data_comunicacao"], "fonte": "rif", "tipo": "comunicacao", "valor": 0, "entidades": ents, "ponteiro": ptr,
                       "descricao": f"comunicação {c['tipo'] or ''} de {c['comunicante'] or '?'}", "marco": True})
    for r in con.execute("select * from relacionamentos_ccs"):
        ents = {r["pessoa"], r["conta"]} - {None}
        for campo, tipo in (("inicio", "inicio_relacionamento"), ("fim", "fim_relacionamento")):
            if r[campo]:
                ev.append({"data": r[campo], "fonte": "ccs", "tipo": tipo, "valor": 0, "entidades": ents,
                           "ponteiro": util.ponteiro(r["doc_id"], f"ccs#{r['linha']}"), "descricao": f"{(r['tipo'] or '').lower()} {r['conta']}", "marco": True})
    tabelas = {l["name"] for l in con.execute("select name from sqlite_master where type='table'")}
    if "eventos_telematicos" in tabelas:
        for e in con.execute("select * from eventos_telematicos"):
            data = (e["ts_utc"] or "")[:10]
            if data:
                ev.append({"data": data, "fonte": "telematico", "tipo": e["tipo"] if "tipo" in e.keys() else "evento", "valor": 0,
                           "entidades": {e[k] for k in e.keys() if k in ("identificador", "conta_usuario") and e[k]},
                           "ponteiro": util.ponteiro(e["doc_id"], f"ev#{e['ev_id']}"), "descricao": e["descricao"] if "descricao" in e.keys() else ""})
    for agente, a in _achados(d):
        per = a.get("periodo") or {}
        if per.get("inicio"):
            ev.append({"data": per["inicio"], "fonte": "achado", "tipo": (a.get("rotulo") or "").lower(), "valor": 0,
                       "entidades": set(a.get("entidades") or []), "ponteiro": f"achado:{agente}:{a.get('id')}",
                       "descricao": (a.get("enunciado") or "")[:120] + (f" (até {per['fim']})" if per.get("fim") else ""), "marco": True})
    if entidade:
        ev = [e for e in ev if entidade in e["entidades"]]
    ev.sort(key=lambda e: (e["data"], e["fonte"], e["ponteiro"]))
    return ev


def integrada(codinome: str, granularidade: str = "mes", entidade: str | None = None, inicio: str | None = None, fim: str | None = None) -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    eventos = _eventos(con, d, entidade)
    con.close()
    if inicio:
        eventos = [e for e in eventos if e["data"] >= inicio]
    if fim:
        eventos = [e for e in eventos if e["data"] <= fim]
    if not eventos:
        raise caso.ErroCaso("nenhum evento em caso.db para os filtros dados (importe fontes com `banco importar`/`rif parse`)")

    serie: dict[str, dict] = {}
    for e in eventos:
        p = _periodo(e["data"], granularidade)
        s = serie.setdefault(p, {"periodo": p, "eventos": 0, "por_fonte": collections.Counter(), "movimentacao": 0, "entidades": set(), "fontes": set()})
        s["eventos"] += 1
        s["por_fonte"][e["fonte"]] += 1
        s["fontes"].add(e["fonte"])
        s["entidades"] |= e["entidades"]
        if e["fonte"] == "bancario":
            s["movimentacao"] += e["valor"]
    pontos = sorted(serie.values(), key=lambda s: s["periodo"])
    mov = [s["movimentacao"] for s in pontos]
    media = statistics.fmean(mov) if mov else 0
    desvio = statistics.pstdev(mov) if len(mov) > 1 else 0
    saida = [{"periodo": s["periodo"], "eventos": s["eventos"], "por_fonte": dict(s["por_fonte"]), "movimentacao": util.reais(s["movimentacao"]),
              "entidades": len(s["entidades"]), "fontes_distintas": len(s["fontes"]), "convergencia": len(s["fontes"] - {"achado"}) >= 2,
              "pico_bancario": len(mov) >= 3 and s["movimentacao"] > media + 1.5 * desvio} for s in pontos]
    marcos = [{"data": e["data"], "fonte": e["fonte"], "tipo": e["tipo"], "descricao": e["descricao"], "entidades": sorted(e["entidades"])[:6], "ponteiro": e["ponteiro"]}
              for e in eventos if e.get("marco")]
    return {
        "codinome": codinome, "parametros": {"granularidade": granularidade, "entidade": entidade, "inicio": inicio, "fim": fim},
        "eventos": len(eventos), "por_fonte": dict(collections.Counter(e["fonte"] for e in eventos)),
        "periodo_inicio": eventos[0]["data"], "periodo_fim": eventos[-1]["data"],
        "picos_bancarios": [s["periodo"] for s in saida if s["pico_bancario"]],
        "periodos_convergentes": [s["periodo"] for s in saida if s["convergencia"]],
        "serie": saida, "marcos": marcos[:200],
    }
