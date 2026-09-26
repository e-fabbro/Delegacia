"""F6 — Societário: `soc importar/qsa/compartilhados/cruzar-bancario`.

Tabelas pj (uma por PJ) e pj_qsa (uma linha por vínculo sócio/administrador). Aceita planilha com uma
linha por sócio (colunas da PJ repetidas) ou tabelas separadas. Só material do caso; nenhuma consulta externa.
Ponteiros: [F:DOC-###:qsa#N] (N = linha do vínculo no documento).
"""
import collections
import datetime as dt
import json
import re

from agencia import banco, caso, db, layouts, util
from agencia.banco import _tabelas, _cel
from agencia.cofre import sem_acentos

LAYOUT = "societario"
TIPOS = ("SOCIETARIO",)


def importar(codinome: str, doc_id: str, layout: str | None = None) -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    doc = con.execute("select tipo from documentos where doc_id=?", (doc_id,)).fetchone()
    if doc is None:
        raise caso.ErroCaso(f"{doc_id} não consta de caso.db; rode `ingerir`")
    if doc["tipo"] not in TIPOS and not layout:
        raise caso.ErroCaso(f"{doc_id} é do tipo {doc['tipo']}; `soc importar` aceita SOCIETARIO (ou --layout para forçar)")
    lay = layouts.carregar(layout or LAYOUT)
    formatos = tuple(lay.get("formato_data") or util.FORMATOS_DATA)
    sep = lay.get("separador_decimal", ",")
    con.execute("delete from pj_qsa where doc_id=?", (doc_id,))
    con.execute("delete from pj where doc_id=?", (doc_id,))
    avisos, pjs, qsa = [], {}, []
    linha_global = 0
    for nome_tab, colunas, linhas in _tabelas(d, doc_id):
        ipj = layouts.resolver_colunas(lay["pj"], colunas)
        iq = layouts.resolver_colunas(lay["qsa"], colunas)
        if "pj" not in ipj:
            avisos.append(f"tabela {nome_tab}: sem coluna de CNPJ; ignorada (ajuste config/layouts/societario.yaml)")
            continue
        for n, linha in enumerate(linhas, start=1):
            linha_global += 1
            pj = util.RE_TOKEN_PESSOA.search(_cel(linha, ipj, "pj") or "") or util.RE_TOKEN_PESSOA.search(_cel(linha, ipj, "razao") or "")
            if not pj:
                avisos.append(f"tabela {nome_tab}, linha {n}: CNPJ não pseudonimizado/identificado; linha ignorada")
                continue
            pj = pj.group(0)
            reg = pjs.setdefault(pj, {"pj": pj, "doc_id": doc_id, "abertura": None, "situacao": None, "cnae": None, "capital_centavos": None,
                                      "uf": None, "municipio": None, "endereco": None, "contatos": set(), "socios": 0})
            reg["abertura"] = reg["abertura"] or util.data_iso(_cel(linha, ipj, "abertura"), formatos)
            reg["situacao"] = reg["situacao"] or (_cel(linha, ipj, "situacao") or None)
            reg["cnae"] = reg["cnae"] or (_cel(linha, ipj, "cnae") or None)
            reg["capital_centavos"] = reg["capital_centavos"] if reg["capital_centavos"] is not None else util.centavos(_cel(linha, ipj, "capital"), sep)
            reg["uf"] = reg["uf"] or (_cel(linha, ipj, "uf") or None)
            reg["municipio"] = reg["municipio"] or (_cel(linha, ipj, "municipio") or None)
            end = _cel(linha, ipj, "endereco")
            reg["endereco"] = reg["endereco"] or (end or None)
            for campo in ("telefone", "email"):
                for tok in re.findall(r"\b(?:TEL|EML)-\d{4}\b", _cel(linha, ipj, campo)):
                    reg["contatos"].add(tok)
            socio = util.RE_TOKEN_PESSOA.search(_cel(linha, iq, "socio") or "") or util.RE_TOKEN_PESSOA.search(_cel(linha, iq, "socio_nome") or "")
            if socio and socio.group(0) != pj:
                reg["socios"] += 1
                qsa.append((doc_id, linha_global, pj, socio.group(0), (_cel(linha, iq, "qualificacao") or None),
                            util.data_iso(_cel(linha, iq, "entrada"), formatos), util.data_iso(_cel(linha, iq, "saida"), formatos)))
    for reg in pjs.values():
        con.execute("insert or replace into pj values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (reg["pj"], doc_id, reg["abertura"], reg["situacao"], reg["cnae"], reg["capital_centavos"], reg["uf"], reg["municipio"],
                     reg["endereco"], json.dumps(sorted(reg["contatos"]))))
    con.executemany("insert into pj_qsa values (?, ?, ?, ?, ?, ?, ?)", qsa)
    con.commit()
    con.close()
    return {"codinome": codinome, "doc_id": doc_id, "pjs": len(pjs), "vinculos_qsa": len(qsa),
            "lista": [{"pj": r["pj"], "abertura": r["abertura"], "capital": util.reais(r["capital_centavos"]), "cnae": r["cnae"], "socios": r["socios"]} for r in pjs.values()],
            "avisos": avisos}


def _carregar(con, pj: str | None = None):
    pjs = [dict(l) for l in con.execute("select * from pj" + (" where pj=?" if pj else "") + " order by pj", (pj,) if pj else ())]
    if not pjs:
        raise caso.ErroCaso("nenhuma PJ importada; rode `soc importar <COD> DOC-###`")
    qsa = [dict(l) for l in con.execute("select * from pj_qsa" + (" where pj=?" if pj else "") + " order by pj, entrada, linha", (pj,) if pj else ())]
    for p in pjs:
        p["capital"] = util.reais(p.pop("capital_centavos"))
        p["contatos"] = json.loads(p["contatos"])
    for q in qsa:
        q["ponteiro"] = util.ponteiro(q["doc_id"], f"qsa#{q['linha']}")
    return pjs, qsa


def _admin(lay: dict, qualificacao: str | None) -> bool:
    plano = sem_acentos(qualificacao or "").upper()
    return any(sem_acentos(a).upper() in plano for a in lay.get("qualificacoes_admin") or [])


def qsa(codinome: str, pj: str | None = None) -> dict:
    lay = layouts.carregar(LAYOUT)
    con = db.abrir_caso(caso.caminho(codinome))
    pjs, vinc = _carregar(con, pj)
    con.close()
    por_pj = collections.defaultdict(list)
    for q in vinc:
        por_pj[q["pj"]].append(q)
    saida = []
    for p in pjs:
        socios = [{"socio": q["socio"], "qualificacao": q["qualificacao"], "administrador": _admin(lay, q["qualificacao"]), "entrada": q["entrada"],
                   "saida": q["saida"], "ativo": q["saida"] is None, "ponteiro": q["ponteiro"]} for q in por_pj[p["pj"]]]
        meses = None
        if p["abertura"]:
            hoje = dt.date.today()
            ab = dt.date.fromisoformat(p["abertura"])
            meses = (hoje.year - ab.year) * 12 + hoje.month - ab.month
        saida.append({**p, "meses_existencia": meses, "socios": socios, "socios_ativos": sum(1 for s in socios if s["ativo"]),
                      "administradores": [s["socio"] for s in socios if s["administrador"] and s["ativo"]],
                      "trocas_societarias": [{"socio": s["socio"], "saida": s["saida"], "ponteiro": s["ponteiro"]} for s in socios if s["saida"]]})
    return {"codinome": codinome, "pjs": len(saida), "lista": saida}


def compartilhados(codinome: str) -> dict:
    con = db.abrir_caso(caso.caminho(codinome))
    pjs, vinc = _carregar(con)
    con.close()
    socios = collections.defaultdict(list)
    for q in vinc:
        socios[q["socio"]].append({"pj": q["pj"], "qualificacao": q["qualificacao"], "entrada": q["entrada"], "saida": q["saida"], "ponteiro": q["ponteiro"]})
    enderecos = collections.defaultdict(set)
    contatos = collections.defaultdict(set)
    for p in pjs:
        if p["endereco"]:
            enderecos[p["endereco"]].add(p["pj"])
        for c in p["contatos"]:
            contatos[c].add(p["pj"])
    return {
        "codinome": codinome,
        "socios_compartilhados": [{"socio": s, "pjs": sorted({v["pj"] for v in vs}), "vinculos": vs} for s, vs in sorted(socios.items()) if len({v["pj"] for v in vs}) > 1],
        "enderecos_compartilhados": [{"endereco": e, "pjs": sorted(p)} for e, p in sorted(enderecos.items()) if len(p) > 1],
        "contatos_compartilhados": [{"contato": c, "pjs": sorted(p)} for c, p in sorted(contatos.items()) if len(p) > 1],
        "socios_por_pj": {p["pj"]: sorted({q["socio"] for q in vinc if q["pj"] == p["pj"]}) for p in pjs},
    }


def cruzar_bancario(codinome: str) -> dict:
    """Porte declarado (capital, CNAE, idade) × movimentação das contas da PJ no caso."""
    lay = layouts.carregar(LAYOUT)
    recente = int(lay.get("recente_meses", 12))
    con = db.abrir_caso(caso.caminho(codinome))
    pjs, vinc = _carregar(con)
    contas = {l["conta"]: dict(l) for l in con.execute("select * from contas")}
    con.close()
    try:
        resumo = {c["conta"]: c for c in banco.resumo(codinome)["contas"]}
        passagem = {c["conta"]: c for c in banco.passagem(codinome)["contas"]}
        picos = banco.linha_tempo(codinome)["picos"]
    except caso.ErroCaso:
        resumo, passagem, picos = {}, {}, []
    saida, avisos = [], []
    for p in pjs:
        minhas = [c for c, v in contas.items() if v["titular"] == p["pj"] and c in resumo]
        if not minhas:
            avisos.append(f"{p['pj']}: sem conta com extrato no caso")
        creditos = sum(round(resumo[c]["creditos"] * 100) for c in minhas)
        debitos = sum(round(resumo[c]["debitos"] * 100) for c in minhas)
        primeira = min((resumo[c]["periodo_inicio"] for c in minhas), default=None)
        meses_ate_mov = None
        if p["abertura"] and primeira:
            ab, pr = dt.date.fromisoformat(p["abertura"]), dt.date.fromisoformat(primeira)
            meses_ate_mov = (pr.year - ab.year) * 12 + pr.month - ab.month
        cap = round((p["capital"] or 0) * 100)
        indicios = []
        if cap and creditos and creditos / cap >= 10:
            indicios.append(f"créditos {round(creditos / cap, 1)}× o capital social")
        if meses_ate_mov is not None and meses_ate_mov <= recente:
            indicios.append(f"abertura {meses_ate_mov} meses antes da primeira movimentação (limiar {recente})")
        if p["abertura"] and picos and any(p["abertura"][:7] <= pico and (int(pico[:4]) * 12 + int(pico[5:7])) - (int(p["abertura"][:4]) * 12 + int(p["abertura"][5:7])) <= recente for pico in picos):
            indicios.append(f"abertura até {recente} meses antes de pico de movimentação do caso ({', '.join(picos)})")
        if any(passagem.get(c, {}).get("perfil_passagem") for c in minhas):
            indicios.append("conta com perfil de passagem")
        saida.append({"pj": p["pj"], "abertura": p["abertura"], "cnae": p["cnae"], "capital": p["capital"], "uf": p["uf"], "contas": minhas,
                      "creditos": util.reais(creditos), "debitos": util.reais(debitos), "razao_creditos_capital": round(creditos / cap, 2) if cap and creditos else None,
                      "primeira_movimentacao": primeira, "meses_abertura_ate_movimentacao": meses_ate_mov,
                      "indice_passagem_max": max((passagem.get(c, {}).get("indice_passagem") or 0 for c in minhas), default=None),
                      "socios": sorted({q["socio"] for q in vinc if q["pj"] == p["pj"]}), "indicios": indicios})
    saida.sort(key=lambda x: (-len(x["indicios"]), -(x["razao_creditos_capital"] or 0)))
    return {"codinome": codinome, "parametros": {"recente_meses": recente, "razao_creditos_capital_min": 10}, "picos_bancarios": picos, "pjs": saida, "avisos": avisos,
            "nota": "indícios são padrões a apurar, não conclusão; cada um deve ser citado com a fonte societária (qsa#) e bancária (tx#/agg#)"}
