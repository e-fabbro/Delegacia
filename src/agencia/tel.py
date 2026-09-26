"""F6 — Telemático: `tel importar/normalizar/ips/sessoes/janela`.

Todo evento vai para eventos_telematicos com ts_original, fuso_origem e ts_utc. O fuso de cada documento
vem, nesta ordem: --fuso (manual), coluna de fuso, nome de coluna com UTC, presunção por tipo (layout)
— e a origem fica registrada em tel_fontes.origem_fuso para a nota do analista.
Ponteiros: [F:DOC-###:ev#N] (N = posição do evento no documento).
"""
import collections
import datetime as dt
import ipaddress
import json
import re
from pathlib import Path

from agencia import caso, db, layouts, util
from agencia.banco import _tabelas, _cel
from agencia.cofre import sem_acentos

LAYOUT = "telematica"
TIPOS = ("TELEMATICA", "ERB", "BILHETAGEM")
RE_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$")
RE_TOKEN_ID = re.compile(r"\b(?:EML|TEL|PF|PJ)-\d{4}\b")


def _offset(lay: dict, fuso: str) -> dt.timedelta:
    nome = (fuso or "").strip()
    tabela = lay.get("fusos") or {}
    if nome in tabela:
        nome = tabela[nome]
    m = RE_OFFSET.match(nome)
    if not m:
        raise caso.ErroCaso(f"fuso desconhecido: {fuso!r}; use um de {sorted(tabela)} ou ±HH:MM")
    sinal = 1 if m.group(1) == "+" else -1
    return sinal * dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))


def _para_utc(texto: str, fuso: str, lay: dict) -> tuple[str | None, str | None]:
    """(ts_original ISO, ts_utc ISO) a partir do texto de data/hora no fuso dado."""
    s = (texto or "").strip()
    if not s:
        return None, None
    if s.endswith("Z"):
        s, fuso = s[:-1], "UTC"
    local = None
    for f in lay.get("formato_data") or util.FORMATOS_DATA:
        try:
            local = dt.datetime.strptime(s, f)
            break
        except ValueError:
            continue
    if local is None:
        return s, None
    utc = local - _offset(lay, fuso)
    return local.isoformat(timespec="seconds"), utc.isoformat(timespec="seconds")


def _fuso_documento(lay: dict, tipo: str, colunas: list[str], idx: dict, fuso_manual: str | None) -> tuple[str, str]:
    if fuso_manual:
        return fuso_manual, "manual"
    if "fuso" in idx:
        return "coluna", "coluna"
    col_data = colunas[idx["data_hora"]] if "data_hora" in idx else colunas[idx["data"]] if "data" in idx else ""
    plano = sem_acentos(col_data).upper()
    if any(re.search(p, plano) for p in lay.get("coluna_utc") or []):
        return "UTC", "nome_da_coluna"
    padrao = (lay.get("fuso_padrao") or {}).get(tipo, "UTC")
    return padrao, f"presuncao_por_tipo_{tipo}"


def _cgnat(lay: dict, ip: str | None) -> bool:
    if not ip:
        return False
    try:
        end = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(end in ipaddress.ip_network(r) for r in lay.get("cgnat") or [])


def importar(codinome: str, doc_id: str, fuso: str | None = None) -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    doc = con.execute("select tipo from documentos where doc_id=?", (doc_id,)).fetchone()
    if doc is None:
        raise caso.ErroCaso(f"{doc_id} não consta de caso.db; rode `ingerir`")
    tipo = doc["tipo"]
    if tipo not in TIPOS and not fuso:
        raise caso.ErroCaso(f"{doc_id} é do tipo {tipo}; `tel importar` aceita TELEMATICA, ERB e BILHETAGEM (ou informe --fuso para forçar)")
    lay = layouts.carregar(LAYOUT)
    mapa = lay["eventos"]
    if fuso:
        _offset(lay, fuso)   # valida
    con.execute("delete from eventos_telematicos where doc_id=?", (doc_id,))
    avisos, registros = [], []
    ev_id, fuso_doc, origem_fuso = 0, None, None
    for nome_tab, colunas, linhas in _tabelas(d, doc_id):
        idx = layouts.resolver_colunas(mapa, colunas)
        if "data_hora" not in idx and "data" not in idx:
            avisos.append(f"tabela {nome_tab}: sem coluna de data/hora; ignorada (ajuste config/layouts/telematica.yaml)")
            continue
        fuso_tab, origem = _fuso_documento(lay, tipo, colunas, idx, fuso)
        fuso_doc, origem_fuso = fuso_doc or fuso_tab, origem_fuso or origem
        vinc_idx = [i for i, c in enumerate(colunas) if any(layouts._norm(c) == layouts._norm(v) for v in lay.get("vinculados") or [])]
        for n, linha in enumerate(linhas, start=1):
            ev_id += 1
            texto_dt = _cel(linha, idx, "data_hora") or (_cel(linha, idx, "data") + " " + _cel(linha, idx, "hora")).strip()
            fuso_linha = _cel(linha, idx, "fuso") if fuso_tab == "coluna" else fuso_tab
            try:
                ts_orig, ts_utc = _para_utc(texto_dt, fuso_linha or "UTC", lay)
            except caso.ErroCaso as e:
                avisos.append(f"ev#{ev_id}: {e}")
                ts_orig, ts_utc = texto_dt, None
            if ts_utc is None:
                avisos.append(f"ev#{ev_id} (tabela {nome_tab}, linha {n}): data/hora não interpretada: {texto_dt!r}")
            porta = _cel(linha, idx, "porta")
            vinculados = sorted({t for i in vinc_idx if i < len(linha) for t in RE_TOKEN_ID.findall(linha[i])})
            ident = _cel(linha, idx, "identificador") or None
            registros.append((
                doc_id, ev_id, ident, (_cel(linha, idx, "tipo") or None), ts_orig, fuso_linha or None, ts_utc,
                _cel(linha, idx, "ip") or None, int(porta) if porta.isdigit() else None, _cel(linha, idx, "dispositivo") or None,
                _cel(linha, idx, "erb") or None, _cel(linha, idx, "lac") or None, _cel(linha, idx, "cell_id") or None,
                _float(_cel(linha, idx, "latitude")), _float(_cel(linha, idx, "longitude")), _cel(linha, idx, "municipio") or None,
                _cel(linha, idx, "contraparte") or None, int(_cel(linha, idx, "duracao")) if _cel(linha, idx, "duracao").isdigit() else None,
                json.dumps([v for v in vinculados if v != ident]), _cel(linha, idx, "descricao") or None, nome_tab, n,
            ))
    con.executemany("insert into eventos_telematicos values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", registros)
    con.execute("insert or replace into tel_fontes values (?, ?, ?, ?, ?, ?)",
                (doc_id, tipo, fuso_doc or "?", origem_fuso or "?", len(registros), caso.agora()))
    con.commit()
    con.close()
    utcs = [r[6] for r in registros if r[6]]
    return {"codinome": codinome, "doc_id": doc_id, "tipo": tipo, "eventos": len(registros), "fuso": fuso_doc, "origem_fuso": origem_fuso,
            "identificadores": sorted({r[2] for r in registros if r[2]}), "periodo_utc_inicio": min(utcs) if utcs else None,
            "periodo_utc_fim": max(utcs) if utcs else None, "avisos": avisos}


def _float(s: str) -> float | None:
    try:
        return float(s.replace(",", ".")) if s else None
    except ValueError:
        return None


def normalizar(codinome: str, doc: str | None = None, fuso: str | None = None) -> dict:
    """Sem --fuso: relata o fuso de cada fonte. Com --doc e --fuso: reimporta o documento nesse fuso."""
    if fuso and not doc:
        raise caso.ErroCaso("--fuso exige --doc (o fuso é declarado por documento)")
    if doc and fuso:
        importar(codinome, doc, fuso=fuso)
    con = db.abrir_caso(caso.caminho(codinome))
    fontes = [dict(l) for l in con.execute("select * from tel_fontes order by doc_id")]
    con.close()
    if not fontes:
        raise caso.ErroCaso("nenhuma fonte telemática importada; rode `tel importar <COD> DOC-###`")
    for f in fontes:
        f["presumido"] = f["origem_fuso"].startswith("presuncao")
    return {"codinome": codinome, "fontes": fontes, "presumidos": [f["doc_id"] for f in fontes if f["presumido"]],
            "nota": "todo ts_utc em UTC; declare na nota o fuso de cada fonte e sua origem (coluna, nome da coluna, manual ou presunção)"}


def _eventos(con, identificador=None, doc=None, inicio=None, fim=None) -> list[dict]:
    cond, args = [], []
    if identificador:
        cond.append("identificador=?"); args.append(identificador)
    if doc:
        cond.append("doc_id=?"); args.append(doc)
    if inicio:
        cond.append("ts_utc>=?"); args.append(inicio)
    if fim:
        cond.append("ts_utc<=?"); args.append(fim)
    w = (" where " + " and ".join(cond)) if cond else ""
    evs = [dict(l) for l in con.execute(f"select * from eventos_telematicos{w} order by identificador, ts_utc, ev_id", args)]
    if not evs:
        raise caso.ErroCaso("nenhum evento telemático para os filtros dados; rode `tel importar`")
    for e in evs:
        e["vinculados"] = json.loads(e["vinculados"])
        e["ponteiro"] = util.ponteiro(e["doc_id"], f"ev#{e['ev_id']}")
    return evs


def ips(codinome: str, identificador: str | None = None, doc: str | None = None) -> dict:
    con = db.abrir_caso(caso.caminho(codinome))
    evs = _eventos(con, identificador, doc)
    con.close()
    lay = layouts.carregar(LAYOUT)
    agg: dict[tuple, dict] = {}
    for e in evs:
        if not e["ip"]:
            continue
        a = agg.setdefault((e["identificador"], e["ip"]), {"identificador": e["identificador"], "ip": e["ip"], "eventos": 0, "com_porta": 0,
                                                            "portas": set(), "primeira_utc": e["ts_utc"], "ultima_utc": e["ts_utc"],
                                                            "dispositivos": set(), "docs": set(), "ponteiros": []})
        a["eventos"] += 1
        if e["porta"] is not None:
            a["com_porta"] += 1
            a["portas"].add(e["porta"])
        if e["ts_utc"]:
            a["primeira_utc"] = min(filter(None, [a["primeira_utc"], e["ts_utc"]]))
            a["ultima_utc"] = max(filter(None, [a["ultima_utc"], e["ts_utc"]]))
        if e["dispositivo"]:
            a["dispositivos"].add(e["dispositivo"])
        a["docs"].add(e["doc_id"])
        a["ponteiros"].append(e["ponteiro"])
    lista = []
    for a in agg.values():
        cg = _cgnat(lay, a["ip"])
        lista.append({**a, "portas": sorted(a["portas"]), "dispositivos": sorted(a["dispositivos"]), "docs": sorted(a["docs"]),
                      "ponteiros": a["ponteiros"][:20], "cgnat": cg, "sem_porta_em_cgnat": cg and a["com_porta"] < a["eventos"]})
    lista.sort(key=lambda a: (-a["eventos"], a["identificador"] or "", a["ip"]))
    por_ip = collections.defaultdict(set)
    for a in lista:
        por_ip[a["ip"]].add(a["identificador"])
    compartilhados = [{"ip": ip, "identificadores": sorted(ids)} for ip, ids in por_ip.items() if len(ids) > 1]
    return {"codinome": codinome, "eventos_com_ip": sum(a["eventos"] for a in lista), "pares_identificador_ip": len(lista),
            "ips_compartilhados": compartilhados,
            "diligencias_porta": [{"identificador": a["identificador"], "ip": a["ip"], "eventos_sem_porta": a["eventos"] - a["com_porta"],
                                   "janela_utc": [a["primeira_utc"], a["ultima_utc"]], "ponteiros": a["ponteiros"][:5]} for a in lista if a["sem_porta_em_cgnat"]],
            "lista": lista, "nota": "IP identifica o assinante da conexão, não o usuário; sem porta lógica em CGNAT a operadora pode não individualizar"}


def _dt(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts)


def _coincidencias_erb(evs: list[dict], tolerancia_min: int) -> list[dict]:
    por_erb = collections.defaultdict(list)
    for e in evs:
        if e["erb"] and e["ts_utc"] and e["identificador"]:
            por_erb[e["erb"]].append(e)
    saida = []
    for erb, lista in por_erb.items():
        lista.sort(key=lambda e: e["ts_utc"])
        for i, a in enumerate(lista):
            for b in lista[i + 1:]:
                if (_dt(b["ts_utc"]) - _dt(a["ts_utc"])).total_seconds() > tolerancia_min * 60:
                    break
                if a["identificador"] != b["identificador"]:
                    saida.append({"erb": erb, "municipio": a["municipio"] or b["municipio"], "identificadores": sorted([a["identificador"], b["identificador"]]),
                                  "ts_utc_a": a["ts_utc"], "ts_utc_b": b["ts_utc"], "minutos": round(abs((_dt(b["ts_utc"]) - _dt(a["ts_utc"])).total_seconds()) / 60, 1),
                                  "ponteiros": [a["ponteiro"], b["ponteiro"]]})
    return saida


def sessoes(codinome: str, identificador: str | None = None, doc: str | None = None, intervalo_min: int | None = None, tolerancia_erb_min: int = 60) -> dict:
    lay = layouts.carregar(LAYOUT)
    intervalo = intervalo_min or int(lay.get("sessao_intervalo_min", 30))
    con = db.abrir_caso(caso.caminho(codinome))
    evs = _eventos(con, identificador, doc)
    con.close()
    por_id = collections.defaultdict(list)
    for e in evs:
        if e["ts_utc"]:
            por_id[e["identificador"] or "?"].append(e)
    lista, vinculos = [], {}
    for ident, lst in por_id.items():
        lst.sort(key=lambda e: e["ts_utc"])
        atual = None
        for e in lst:
            nova = atual is None or e["ip"] != atual["ip"] or (_dt(e["ts_utc"]) - _dt(atual["fim_utc"])).total_seconds() > intervalo * 60
            if nova:
                atual = {"identificador": ident, "inicio_utc": e["ts_utc"], "fim_utc": e["ts_utc"], "ip": e["ip"], "portas": set(), "eventos": 0,
                         "tipos": [], "dispositivos": set(), "erbs": set(), "ponteiros": []}
                lista.append(atual)
            atual["fim_utc"] = e["ts_utc"]
            atual["eventos"] += 1
            if e["porta"] is not None:
                atual["portas"].add(e["porta"])
            if e["tipo"]:
                atual["tipos"].append(e["tipo"])
            if e["dispositivo"]:
                atual["dispositivos"].add(e["dispositivo"])
            if e["erb"]:
                atual["erbs"].add(e["erb"])
            atual["ponteiros"].append(e["ponteiro"])
            for v in e["vinculados"]:
                vinculos.setdefault(ident, {}).setdefault(v, []).append(e["ponteiro"])
            if e["contraparte"]:
                vinculos.setdefault(ident, {}).setdefault(e["contraparte"], []).append(e["ponteiro"])
    for s in lista:
        s["duracao_min"] = round((_dt(s["fim_utc"]) - _dt(s["inicio_utc"])).total_seconds() / 60, 1)
        s["portas"], s["dispositivos"], s["erbs"] = sorted(s["portas"]), sorted(s["dispositivos"]), sorted(s["erbs"])
        s["ponteiros"] = s["ponteiros"][:20]
    lista.sort(key=lambda s: (s["identificador"], s["inicio_utc"]))
    identificadores = [{"identificador": i, "sessoes": sum(1 for s in lista if s["identificador"] == i), "eventos": len(por_id[i]),
                        "ips": sorted({e["ip"] for e in por_id[i] if e["ip"]}), "dispositivos": sorted({e["dispositivo"] for e in por_id[i] if e["dispositivo"]}),
                        "vinculados": [{"identificador": v, "eventos": len(p), "ponteiros": p[:5]} for v, p in sorted(vinculos.get(i, {}).items())],
                        "primeira_utc": por_id[i][0]["ts_utc"], "ultima_utc": por_id[i][-1]["ts_utc"]} for i in sorted(por_id)]
    inverso = collections.defaultdict(set)
    for i, vs in vinculos.items():
        for v in vs:
            inverso[v].add(i)
    return {"codinome": codinome, "parametros": {"intervalo_min": intervalo, "tolerancia_erb_min": tolerancia_erb_min}, "sessoes": len(lista),
            "identificadores": identificadores,
            "identificadores_compartilhados": [{"vinculado": v, "identificadores": sorted(ids)} for v, ids in inverso.items() if len(ids) > 1],
            "coincidencias_erb": _coincidencias_erb(evs, tolerancia_erb_min), "lista": lista}


def janela(codinome: str, inicio: str, fim: str, fuso_entrada: str = "UTC", identificador: str | None = None, tolerancia_erb_min: int = 60) -> dict:
    """Quem estava conectado, de onde, entre inicio e fim (interpretados em fuso_entrada; padrão UTC)."""
    lay = layouts.carregar(LAYOUT)
    _, ini_utc = _para_utc(inicio, fuso_entrada, lay)
    _, fim_utc = _para_utc(fim, fuso_entrada, lay)
    if not ini_utc or not fim_utc:
        raise caso.ErroCaso("--inicio/--fim no formato AAAA-MM-DD HH:MM:SS (ou dd/mm/aaaa HH:MM:SS)")
    if fim_utc < ini_utc:
        raise caso.ErroCaso("--fim anterior a --inicio")
    con = db.abrir_caso(caso.caminho(codinome))
    try:
        evs = _eventos(con, identificador, None, ini_utc, fim_utc)
    except caso.ErroCaso:
        evs = []
    con.close()
    por_id = collections.defaultdict(list)
    for e in evs:
        por_id[e["identificador"] or "?"].append(e)
    presentes = [{"identificador": i, "eventos": len(lst), "ips": sorted({e["ip"] for e in lst if e["ip"]}), "portas": sorted({e["porta"] for e in lst if e["porta"] is not None}),
                  "tipos": sorted({e["tipo"] for e in lst if e["tipo"]}), "erbs": sorted({e["erb"] for e in lst if e["erb"]}),
                  "municipios": sorted({e["municipio"] for e in lst if e["municipio"]}), "primeiro_utc": lst[0]["ts_utc"], "ultimo_utc": lst[-1]["ts_utc"],
                  "docs": sorted({e["doc_id"] for e in lst}), "ponteiros": [e["ponteiro"] for e in lst][:20]} for i, lst in sorted(por_id.items())]
    return {"codinome": codinome, "janela_utc": [ini_utc, fim_utc], "fuso_entrada": fuso_entrada, "eventos": len(evs), "identificadores_presentes": presentes,
            "coincidencias_erb": _coincidencias_erb(evs, tolerancia_erb_min),
            "eventos_lista": [{k: e[k] for k in ("ponteiro", "identificador", "tipo", "ts_utc", "ts_original", "fuso_origem", "ip", "porta", "erb", "municipio", "contraparte")} for e in evs][:200]}
