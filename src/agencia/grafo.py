"""F4 — Grafo de vínculos: `grafo construir/centrais/exportar`.

Nós = pseudônimos (PF, PJ, CT, TEL, EML, PIX, END). Arestas (tabela `vinculos`) derivadas de caso.db:
- transferencia: conta/titular -> contraparte (transacoes), com n, total e ponteiros tx#;
- rif: titular -> envolvido de cada comunicação (papel), ponteiros com#;
- ccs: pessoa -> conta (titular, procurador...), ponteiros l#;
- achado: entidades co-citadas em achados de 03_analises/*/achados.jsonl (fonte achado:<agente>:<id>).
Exportação em HTML autocontido (sem CDN: a VPS não tem rede), JSON e GraphML, sempre pseudonimizada.
"""
import collections
import html
import json
import math
from pathlib import Path

import networkx as nx

from agencia import caso, db, util

TIPOS_ARESTA = ("transferencia", "rif", "ccs", "telematico", "societario", "cripto", "achado")
MAX_FONTES = 12
RE_TOKEN_NO = __import__("re").compile(r"(PF|PJ|CT|TEL|EML|PIX|END)-\d{4}")


def _titulares(con) -> dict[str, str | None]:
    return {l["conta"]: l["titular"] for l in con.execute("select conta, titular from contas")}


def _achados(d: Path) -> list[tuple[str, dict]]:
    saida = []
    for arq in sorted((d / "03_analises").glob("*/achados.jsonl")):
        if arq.parent.name.startswith("_"):
            continue
        for linha in arq.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(linha) if linha.strip() else None
            except json.JSONDecodeError:
                a = None
            if isinstance(a, dict):
                saida.append((arq.parent.name, a))
    return saida


class _Acumulador:
    def __init__(self):
        self.arestas: dict[tuple[str, str, str], dict] = {}

    def add(self, origem: str | None, destino: str | None, tipo: str, fonte: str, valor: int = 0, data: str | None = None, papel: str | None = None):
        if not origem or not destino or origem == destino:
            return
        a = self.arestas.setdefault((origem, destino, tipo), {"origem": origem, "destino": destino, "tipo": tipo, "n": 0, "total_centavos": 0,
                                                              "primeira": None, "ultima": None, "fontes": [], "papeis": set()})
        a["n"] += 1
        a["total_centavos"] += valor
        if data:
            a["primeira"] = min(filter(None, [a["primeira"], data]))
            a["ultima"] = max(filter(None, [a["ultima"], data]))
        if papel:
            a["papeis"].add(papel)
        if fonte not in a["fontes"] and len(a["fontes"]) < MAX_FONTES:
            a["fontes"].append(fonte)


def construir(codinome: str, sem_achados: bool = False) -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    tit = _titulares(con)
    acc = _Acumulador()

    for t in con.execute("select * from transacoes"):
        proprio = tit.get(t["conta"]) or t["conta"]
        outro = tit.get(t["contraparte_conta"]) or t["contraparte"] or t["contraparte_conta"]
        if not outro:
            continue
        de, para = (proprio, outro) if t["natureza"] == "D" else (outro, proprio)
        acc.add(de, para, "transferencia", util.ponteiro(t["doc_id"], f"tx#{t['tx_id']}"), t["valor_centavos"], t["data"])
    for c in con.execute("select * from comunicacoes_rif"):
        ptr = util.ponteiro(c["doc_id"], f"com#{c['num']}")
        for e in json.loads(c["envolvidos"]):
            if e["papel"] != "titular":
                acc.add(c["titular"], e["pseudonimo"], "rif", ptr, c["valor_centavos"] or 0, c["periodo_inicio"] or c["data_comunicacao"], e["papel"])
    for r in con.execute("select * from relacionamentos_ccs"):
        acc.add(r["pessoa"], r["conta"], "ccs", util.ponteiro(r["doc_id"], f"ccs#{r['linha']}"), 0, r["inicio"], (r["tipo"] or "").lower())
    for e in con.execute("select * from eventos_telematicos"):
        ident = e["identificador"] if e["identificador"] and RE_TOKEN_NO.fullmatch(e["identificador"]) else None
        ptr = util.ponteiro(e["doc_id"], f"ev#{e['ev_id']}")
        data = (e["ts_utc"] or "")[:10] or None
        for v in json.loads(e["vinculados"]):
            acc.add(ident, v, "telematico", ptr, 0, data, "vinculado")
        if e["contraparte"] and RE_TOKEN_NO.fullmatch(e["contraparte"]):
            acc.add(ident, e["contraparte"], "telematico", ptr, 0, data, (e["tipo"] or "contato").lower())
    for q in con.execute("select * from pj_qsa"):
        acc.add(q["socio"], q["pj"], "societario", util.ponteiro(q["doc_id"], f"qsa#{q['linha']}"), 0, q["entrada"], (q["qualificacao"] or "socio").lower())
    for p in con.execute("select pj, endereco, contatos, doc_id from pj"):
        if p["endereco"] and RE_TOKEN_NO.fullmatch(p["endereco"]):
            acc.add(p["pj"], p["endereco"], "societario", f"pj:{p['pj']}", 0, None, "endereco")
        for c in json.loads(p["contatos"]):
            acc.add(p["pj"], c, "societario", f"pj:{p['pj']}", 0, None, "contato")
    for m in con.execute("select * from cripto_movs"):
        ptr = util.ponteiro(m["doc_id"], f"mov#{m['mov_id']}")
        data = (m["ts_utc"] or "")[:10] or None
        ex = m["exchange"] if m["exchange"] and RE_TOKEN_NO.fullmatch(m["exchange"]) else None
        if m["tipo"] == "deposito_fiat":
            acc.add(tit.get(m["contraparte_conta"]) or m["contraparte_conta"], m["cliente"], "cripto", ptr, m["valor_centavos"] or 0, data, "deposito_fiat")
        elif m["tipo"] == "saque_fiat":
            acc.add(m["cliente"], tit.get(m["contraparte_conta"]) or m["contraparte_conta"], "cripto", ptr, m["valor_centavos"] or 0, data, "saque_fiat")
        if ex:
            acc.add(m["cliente"], ex, "cripto", ptr, 0, data, "cliente")
    n_achados = 0
    if not sem_achados:
        for agente, a in _achados(d):
            ents = sorted(set(a.get("entidades") or []))
            n_achados += 1
            for i, x in enumerate(ents):
                for y in ents[i + 1:]:
                    acc.add(x, y, "achado", f"achado:{agente}:{a.get('id')}", 0, (a.get("periodo") or {}).get("inicio"), a.get("rotulo"))

    con.execute("delete from vinculos")
    con.executemany(
        "insert into vinculos values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(a["origem"], a["destino"], a["tipo"], a["n"], a["total_centavos"], a["primeira"], a["ultima"],
          json.dumps(a["fontes"], ensure_ascii=False), json.dumps(sorted(a["papeis"]), ensure_ascii=False)) for a in acc.arestas.values()],
    )
    nos = {x for k in acc.arestas for x in k[:2]}
    con.executemany("insert or ignore into entidades (pseudonimo, tipo, primeiro_doc) values (?, ?, null)", [(n, n.split("-")[0]) for n in nos])
    con.commit()
    con.close()
    g = _grafo_simples(acc.arestas.values())
    componentes = list(nx.connected_components(g))
    return {
        "codinome": codinome, "nos": len(nos), "arestas": len(acc.arestas),
        "por_tipo": dict(collections.Counter(k[2] for k in acc.arestas)),
        "nos_por_tipo": dict(collections.Counter(n.split("-")[0] for n in nos)),
        "achados_lidos": n_achados,
        "componentes": len(componentes),
        "maior_componente": max((len(c) for c in componentes), default=0),
    }


def _carregar(con) -> list[dict]:
    linhas = [dict(l) for l in con.execute("select * from vinculos")]
    if not linhas:
        raise caso.ErroCaso("grafo vazio; rode `grafo construir <COD>` após importar as fontes")
    for l in linhas:
        l["fontes"] = json.loads(l["fontes"])
        l["papeis"] = json.loads(l["papeis"])
    return linhas


def _grafo_simples(arestas) -> nx.Graph:
    g = nx.Graph()
    for a in arestas:
        o, dst = a["origem"], a["destino"]
        if g.has_edge(o, dst):
            g[o][dst]["peso"] += a["n"]
            g[o][dst]["total"] += a["total_centavos"]
            g[o][dst]["tipos"].add(a["tipo"])
        else:
            g.add_edge(o, dst, peso=a["n"], total=a["total_centavos"], tipos={a["tipo"]})
    return g


def _dirigido(arestas) -> nx.DiGraph:
    g = nx.DiGraph()
    for a in arestas:
        if g.has_edge(a["origem"], a["destino"]):
            g[a["origem"]][a["destino"]]["peso"] += a["n"]
            g[a["origem"]][a["destino"]]["total"] += a["total_centavos"]
        else:
            g.add_edge(a["origem"], a["destino"], peso=a["n"], total=a["total_centavos"])
    return g


def centrais(codinome: str, top: int = 10, tipo: str | None = None) -> dict:
    con = db.abrir_caso(caso.caminho(codinome))
    arestas = _carregar(con)
    con.close()
    if tipo:
        arestas = [a for a in arestas if a["tipo"] == tipo]
        if not arestas:
            raise caso.ErroCaso(f"nenhuma aresta do tipo {tipo!r}")
    g = _grafo_simples(arestas)
    dg = _dirigido(arestas)
    inter = nx.betweenness_centrality(g, weight=None, normalized=True)
    comp_de = {}
    for i, comp in enumerate(sorted(nx.connected_components(g), key=lambda c: (-len(c), sorted(c)[0])), start=1):
        for n in comp:
            comp_de[n] = i
    pontes = set(nx.articulation_points(g))
    fontes_por_no = collections.defaultdict(set)
    for a in arestas:
        fontes_por_no[a["origem"]].add(a["tipo"])
        fontes_por_no[a["destino"]].add(a["tipo"])
    nos = []
    for n in g.nodes:
        entradas = dg.in_degree(n, weight="total") if n in dg else 0
        saidas = dg.out_degree(n, weight="total") if n in dg else 0
        nos.append({
            "no": n, "tipo": n.split("-")[0], "grau": g.degree(n), "vizinhos": sorted(g.neighbors(n)),
            "intermediacao": round(inter[n], 4), "ponte": n in pontes, "componente": comp_de[n],
            "fontes": sorted(fontes_por_no[n]), "convergencia": len(fontes_por_no[n]) >= 2,
            "total_recebido": util.reais(entradas), "total_enviado": util.reais(saidas),
            "lancamentos": sum(a["n"] for a in arestas if a["tipo"] == "transferencia" and n in (a["origem"], a["destino"])),
        })
    nos.sort(key=lambda x: (-x["grau"], -x["intermediacao"], x["no"]))
    return {
        "codinome": codinome, "parametros": {"top": top, "tipo": tipo}, "nos": g.number_of_nodes(), "arestas": g.number_of_edges(),
        "componentes": max(comp_de.values(), default=0),
        "pontes": sorted(pontes),
        "convergentes": [x["no"] for x in nos if x["convergencia"]],
        "centrais": nos[:top],
        "por_intermediacao": [x["no"] for x in sorted(nos, key=lambda x: -x["intermediacao"])[:top]],
    }


# ---------- exportar ----------

CORES = {"PF": "#1f77b4", "PJ": "#d62728", "CT": "#2ca02c", "TEL": "#9467bd", "EML": "#8c564b", "PIX": "#e377c2", "END": "#7f7f7f"}
CORES_ARESTA = {"transferencia": "#2ca02c", "rif": "#d62728", "ccs": "#1f77b4", "telematico": "#9467bd", "societario": "#8c564b", "cripto": "#e377c2", "achado": "#ff7f0e"}


def _posicoes(g: nx.Graph) -> dict[str, tuple[float, float]]:
    if g.number_of_nodes() == 0:
        return {}
    pos = nx.spring_layout(g, seed=7, k=1.6 / math.sqrt(g.number_of_nodes()), iterations=200)
    xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    esc = lambda v, lo, hi: 80 + (v - lo) / ((hi - lo) or 1) * 840
    return {n: (round(esc(p[0], minx, maxx), 1), round(esc(p[1], miny, maxy), 1)) for n, p in pos.items()}


def _dados(codinome: str, arestas: list[dict]) -> dict:
    g = _grafo_simples(arestas)
    pos = _posicoes(g)
    cen = centrais(codinome, top=len(g.nodes) or 1)
    info = {x["no"]: x for x in cen["centrais"]}
    nos = [{"id": n, "tipo": n.split("-")[0], "x": pos[n][0], "y": pos[n][1], "grau": info[n]["grau"], "ponte": info[n]["ponte"],
            "componente": info[n]["componente"], "fontes": info[n]["fontes"], "intermediacao": info[n]["intermediacao"]} for n in sorted(g.nodes)]
    ars = [{"origem": a["origem"], "destino": a["destino"], "tipo": a["tipo"], "n": a["n"], "total": util.reais(a["total_centavos"]),
            "primeira": a["primeira"], "ultima": a["ultima"], "papeis": a["papeis"], "fontes": a["fontes"]} for a in arestas]
    return {"codinome": codinome, "nos": nos, "arestas": ars, "gerado_em": caso.agora()}


def _html(dados: dict) -> str:
    esc = html.escape
    pos = {n["id"]: (n["x"], n["y"]) for n in dados["nos"]}
    linhas = []
    for a in dados["arestas"]:
        (x1, y1), (x2, y2) = pos[a["origem"]], pos[a["destino"]]
        titulo = f"{a['origem']} → {a['destino']} · {a['tipo']} · n={a['n']} · R$ {a['total']:.2f} · {a['primeira'] or ''}..{a['ultima'] or ''} · {' '.join(a['fontes'][:4])}"
        largura = 1 + min(6, math.log1p(a["n"]))
        linhas.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{CORES_ARESTA.get(a["tipo"], "#999")}" stroke-width="{largura:.1f}" '
                      f'marker-end="url(#seta)" opacity="0.75" data-tipo="{a["tipo"]}"><title>{esc(titulo)}</title></line>')
    for n in dados["nos"]:
        r = 8 + 3 * math.sqrt(n["grau"])
        borda = ' stroke="#000" stroke-width="3"' if n["ponte"] else ' stroke="#fff" stroke-width="1.5"'
        titulo = f"{n['id']} · grau {n['grau']} · intermediação {n['intermediacao']} · fontes: {', '.join(n['fontes'])}" + (" · PONTE" if n["ponte"] else "")
        linhas.append(f'<g class="no" data-id="{n["id"]}" data-tipo="{n["tipo"]}"><circle cx="{n["x"]}" cy="{n["y"]}" r="{r:.1f}" fill="{CORES.get(n["tipo"], "#999")}"{borda}>'
                      f'<title>{esc(titulo)}</title></circle><text x="{n["x"]}" y="{n["y"] + r + 12:.1f}" text-anchor="middle" font-size="11">{esc(n["id"])}</text></g>')
    legenda = " ".join(f'<span style="color:{c}">■ {t}</span>' for t, c in CORES.items()) + " · " + \
              " ".join(f'<label><input type="checkbox" checked data-tipo="{t}"> <span style="color:{c}">— {t}</span></label>' for t, c in CORES_ARESTA.items())
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Grafo de vínculos — {esc(dados['codinome'])}</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;background:#fafafa}} header{{padding:8px 12px;border-bottom:1px solid #ddd;background:#fff;font-size:13px}}
svg{{width:100vw;height:calc(100vh - 44px);cursor:grab}} text{{pointer-events:none;fill:#222}} .no.apagado circle{{opacity:.15}} line.apagado{{display:none}}
#info{{position:fixed;right:10px;bottom:10px;max-width:420px;background:#fff;border:1px solid #ccc;padding:8px;font-size:12px;white-space:pre-wrap;display:none}}</style></head>
<body><header><b>{esc(dados['codinome'])}</b> · {len(dados['nos'])} nós · {len(dados['arestas'])} arestas · gerado {esc(dados['gerado_em'])} · só pseudônimos · {legenda}
· arraste para mover, roda para zoom, clique num nó para destacar</header>
<svg id="g" viewBox="0 0 1000 1000" xmlns="http://www.w3.org/2000/svg"><defs><marker id="seta" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#888"/></marker></defs>
<g id="cena">{''.join(linhas)}</g></svg>
<div id="info"></div>
<script id="dados" type="application/json">{json.dumps(dados, ensure_ascii=False)}</script>
<script>
(function(){{
 const svg=document.getElementById('g'), cena=document.getElementById('cena'), info=document.getElementById('info');
 const dados=JSON.parse(document.getElementById('dados').textContent);
 let vb=[0,0,1000,1000], arrasto=null;
 const aplicar=()=>svg.setAttribute('viewBox',vb.join(' '));
 svg.addEventListener('wheel',e=>{{e.preventDefault();const f=e.deltaY>0?1.1:0.9;const r=svg.getBoundingClientRect();
   const mx=vb[0]+(e.clientX-r.left)/r.width*vb[2], my=vb[1]+(e.clientY-r.top)/r.height*vb[3];
   vb=[mx-(mx-vb[0])*f,my-(my-vb[1])*f,vb[2]*f,vb[3]*f];aplicar();}},{{passive:false}});
 svg.addEventListener('mousedown',e=>{{arrasto=[e.clientX,e.clientY];}});
 svg.addEventListener('mousemove',e=>{{if(!arrasto)return;const r=svg.getBoundingClientRect();
   vb[0]-=(e.clientX-arrasto[0])/r.width*vb[2];vb[1]-=(e.clientY-arrasto[1])/r.height*vb[3];arrasto=[e.clientX,e.clientY];aplicar();}});
 svg.addEventListener('mouseup',()=>arrasto=null); svg.addEventListener('mouseleave',()=>arrasto=null);
 let sel=null;
 cena.querySelectorAll('.no').forEach(g=>g.addEventListener('click',e=>{{e.stopPropagation();const id=g.dataset.id;
   if(sel===id){{sel=null;cena.querySelectorAll('.no,line').forEach(x=>x.classList.remove('apagado'));info.style.display='none';return;}}
   sel=id;const viz=new Set([id]);const ars=dados.arestas.filter(a=>a.origem===id||a.destino===id);ars.forEach(a=>{{viz.add(a.origem);viz.add(a.destino);}});
   cena.querySelectorAll('.no').forEach(x=>x.classList.toggle('apagado',!viz.has(x.dataset.id)));
   cena.querySelectorAll('line').forEach(x=>{{const t=x.querySelector('title').textContent;x.classList.toggle('apagado',!t.startsWith(id+' ')&&!t.includes('→ '+id+' '));}});
   info.style.display='block';info.textContent=id+'\\n'+ars.map(a=>a.origem+' → '+a.destino+' ['+a.tipo+'] n='+a.n+' R$ '+a.total.toFixed(2)+(a.papeis.length?' '+a.papeis.join('/'):'')+'\\n  '+a.fontes.slice(0,4).join(' ')).join('\\n');}}));
 svg.addEventListener('click',()=>{{sel=null;cena.querySelectorAll('.no,line').forEach(x=>x.classList.remove('apagado'));info.style.display='none';}});
 document.querySelectorAll('header input[type=checkbox]').forEach(cb=>cb.addEventListener('change',()=>{{
   cena.querySelectorAll('line[data-tipo="'+cb.dataset.tipo+'"]').forEach(l=>l.style.display=cb.checked?'':'none');}}));
}})();
</script></body></html>
"""


def exportar(codinome: str, formato: str = "html") -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    arestas = _carregar(con)
    con.close()
    dados = _dados(codinome, arestas)
    pasta = d / "04_produtos"
    saidas = {}
    if formato in ("html", "todos"):
        (pasta / "grafo.html").write_text(_html(dados), encoding="utf-8")
        saidas["html"] = "04_produtos/grafo.html"
    if formato in ("json", "todos"):
        (pasta / "grafo.json").write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        saidas["json"] = "04_produtos/grafo.json"
    if formato in ("graphml", "todos"):
        g = _dirigido(arestas)
        for n in g.nodes:
            g.nodes[n]["tipo"] = n.split("-")[0]
        nx.write_graphml(g, pasta / "grafo.graphml")
        saidas["graphml"] = "04_produtos/grafo.graphml"
    return {"codinome": codinome, "nos": len(dados["nos"]), "arestas": len(dados["arestas"]), "arquivos": saidas}
