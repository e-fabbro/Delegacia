"""F3 — Bancário: `banco importar/integridade/resumo/contrapartes/lancamentos/especie/fracionamento/
passagem/circularidade/cruzar-alvos/linha-tempo`.

Fonte: CSVs pseudonimizados de 02_extraido/DOC-###.tabelas/ (SIMBA, CCS), lidos via config/layouts/.
Valores em centavos (inteiros) internamente; saída em reais com 2 casas. Ponteiros: [F:DOC-###:tx#N],
onde N é a posição do lançamento no documento (1..n, ordem do arquivo).
"""
import collections
import csv
import datetime as dt
import json
import re
import statistics
from pathlib import Path

import networkx as nx

from agencia import caso, db, layouts, util
from agencia.cofre import sem_acentos

TIPOS_EXTRATO = ("SIMBA", "EXTRATO", "PIX")
TIPOS_CCS = ("CCS",)
CAMPOS_MINIMOS_EXTRATO = ("conta", "data", "valor")


# ---------- leitura de tabelas extraídas ----------

def _tabelas(d: Path, doc_id: str) -> list[tuple[str, list[str], list[list[str]]]]:
    pasta = d / "02_extraido" / f"{doc_id}.tabelas"
    if not pasta.is_dir():
        raise caso.ErroCaso(f"{doc_id} não tem tabelas extraídas em 02_extraido/; formato sem tabela ou não ingerido")
    saida = []
    for arq in sorted(pasta.glob("*.csv")):
        with open(arq, encoding="utf-8", newline="") as f:
            linhas = list(csv.reader(f, delimiter=";"))
        if len(linhas) >= 2:
            saida.append((arq.stem, linhas[0], linhas[1:]))
    return saida


def _cel(linha: list[str], idx: dict[str, int], campo: str) -> str:
    i = idx.get(campo)
    return linha[i].strip() if i is not None and i < len(linha) else ""


def _token(valor: str, rx: re.Pattern) -> str | None:
    m = rx.search(valor or "")
    return m.group(0) if m else None


def _natureza(lay: dict, texto: str, valor: int | None) -> str | None:
    t = sem_acentos(texto or "").strip().upper()
    if t in {sem_acentos(x).upper() for x in lay.get("natureza_credito", ["C"])}:
        return "C"
    if t in {sem_acentos(x).upper() for x in lay.get("natureza_debito", ["D"])}:
        return "D"
    if not t and valor is not None:
        return "D" if valor < 0 else "C"
    return None


# ---------- importar ----------

def importar(codinome: str, doc_id: str, layout: str | None = None) -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    doc = con.execute("select tipo from documentos where doc_id=?", (doc_id,)).fetchone()
    if doc is None:
        raise caso.ErroCaso(f"{doc_id} não consta de caso.db; rode `ingerir`")
    tipo = doc["tipo"]
    if tipo in TIPOS_CCS or layout == "ccs":
        r = _importar_ccs(d, con, doc_id, layouts.carregar(layout or "ccs"))
    elif tipo in TIPOS_EXTRATO or layout:
        r = _importar_extrato(d, con, doc_id, layouts.carregar(layout or "simba"))
    else:
        con.close()
        raise caso.ErroCaso(f"{doc_id} é do tipo {tipo}; `banco importar` aceita SIMBA, EXTRATO, PIX e CCS (ou --layout)")
    con.commit()
    con.close()
    return {"codinome": codinome, "doc_id": doc_id, "tipo": tipo, **r}


def _importar_extrato(d: Path, con, doc_id: str, lay: dict) -> dict:
    mapa = lay["extrato"]
    formatos = tuple(lay.get("formato_data") or util.FORMATOS_DATA)
    sep = lay.get("separador_decimal", ",")
    con.execute("delete from transacoes where doc_id=?", (doc_id,))
    avisos, linhas_db, contas_doc = [], [], {}
    tx_id = 0
    for nome_tab, colunas, linhas in _tabelas(d, doc_id):
        idx = layouts.resolver_colunas(mapa, colunas)
        faltam = [c for c in CAMPOS_MINIMOS_EXTRATO if c not in idx]
        if faltam:
            avisos.append(f"tabela {nome_tab}: colunas ausentes {faltam}; tabela ignorada (ajuste config/layouts/simba.yaml)")
            continue
        for n, linha in enumerate(linhas, start=1):
            tx_id += 1
            conta = _token(_cel(linha, idx, "conta"), util.RE_TOKEN_CONTA) or _token(_cel(linha, idx, "agencia"), util.RE_TOKEN_CONTA)
            data = util.data_iso(_cel(linha, idx, "data"), formatos)
            valor = util.centavos(_cel(linha, idx, "valor"), sep)
            natureza = _natureza(lay, _cel(linha, idx, "natureza"), valor)
            if not conta or not data or valor is None or natureza is None:
                avisos.append(f"tx#{tx_id} (tabela {nome_tab}, linha {n}): conta/data/valor/natureza inválidos; lançamento ignorado")
                continue
            od_pessoa = _token(_cel(linha, idx, "od_cpf_cnpj"), util.RE_TOKEN_PESSOA) or _token(_cel(linha, idx, "od_nome"), util.RE_TOKEN_PESSOA)
            od_conta = _token(_cel(linha, idx, "od_conta"), util.RE_TOKEN_CONTA) or _token(_cel(linha, idx, "od_agencia"), util.RE_TOKEN_CONTA)
            banco = _cel(linha, idx, "banco") or None
            titular = _token(_cel(linha, idx, "titular"), util.RE_TOKEN_PESSOA)
            linhas_db.append((
                doc_id, tx_id, conta, banco, data, _cel(linha, idx, "historico") or None, _cel(linha, idx, "documento") or None,
                abs(valor), natureza, util.centavos(_cel(linha, idx, "saldo"), sep), od_pessoa, od_conta,
                _cel(linha, idx, "od_banco") or None, _cel(linha, idx, "local") or None, nome_tab, n,
            ))
            c = contas_doc.setdefault(conta, {"conta": conta, "banco": banco, "titular": titular, "lancamentos": 0})
            c["lancamentos"] += 1
            c["titular"] = c["titular"] or titular
    con.executemany("insert into transacoes values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", linhas_db)
    for c in contas_doc.values():
        atual = con.execute("select titular from contas where conta=?", (c["conta"],)).fetchone()
        con.execute(
            "insert into contas (conta, banco, titular, doc_extrato, origem) values (?, ?, ?, ?, 'SIMBA')"
            " on conflict(conta) do update set banco=coalesce(contas.banco, excluded.banco), titular=coalesce(contas.titular, excluded.titular),"
            " doc_extrato=excluded.doc_extrato",
            (c["conta"], c["banco"], c["titular"], doc_id),
        )
        c["titular"] = (atual["titular"] if atual and atual["titular"] else None) or c["titular"]
    datas = [l[4] for l in linhas_db]
    return {
        "transacoes": len(linhas_db),
        "contas": sorted(contas_doc.values(), key=lambda c: c["conta"]),
        "periodo_inicio": min(datas) if datas else None,
        "periodo_fim": max(datas) if datas else None,
        "avisos": avisos,
    }


def _importar_ccs(d: Path, con, doc_id: str, lay: dict) -> dict:
    mapa = lay["relacionamentos"]
    formatos = tuple(lay.get("formato_data") or util.FORMATOS_DATA)
    tipos_titular = {sem_acentos(t).upper() for t in lay.get("tipos_titular", ["TITULAR"])}
    con.execute("delete from relacionamentos_ccs where doc_id=?", (doc_id,))
    avisos, registros = [], []
    n_total = 0
    for nome_tab, colunas, linhas in _tabelas(d, doc_id):
        idx = layouts.resolver_colunas(mapa, colunas)
        if "conta" not in idx or ("pessoa" not in idx and "nome" not in idx):
            avisos.append(f"tabela {nome_tab}: colunas de conta/pessoa ausentes; tabela ignorada (ajuste config/layouts/ccs.yaml)")
            continue
        for n, linha in enumerate(linhas, start=1):
            n_total += 1
            pessoa = _token(_cel(linha, idx, "pessoa"), util.RE_TOKEN_PESSOA) or _token(_cel(linha, idx, "nome"), util.RE_TOKEN_PESSOA)
            conta = _token(_cel(linha, idx, "conta"), util.RE_TOKEN_CONTA) or _token(_cel(linha, idx, "agencia"), util.RE_TOKEN_CONTA)
            if not conta:
                avisos.append(f"tabela {nome_tab}, linha {n}: conta não identificada; ignorada")
                continue
            tipo = sem_acentos(_cel(linha, idx, "tipo") or "TITULAR").upper()
            registros.append((doc_id, n_total, pessoa, conta, _cel(linha, idx, "banco") or None, tipo,
                              util.data_iso(_cel(linha, idx, "inicio"), formatos), util.data_iso(_cel(linha, idx, "fim"), formatos)))
    con.executemany("insert into relacionamentos_ccs values (?, ?, ?, ?, ?, ?, ?, ?)", registros)
    for _, _, pessoa, conta, banco, tipo, _, _ in registros:
        con.execute("insert or ignore into contas (conta, banco, titular, doc_extrato, origem) values (?, ?, null, null, 'CCS')", (conta, banco))
        if tipo in tipos_titular and pessoa:
            con.execute("update contas set titular=coalesce(titular, ?), banco=coalesce(banco, ?) where conta=?", (pessoa, banco, conta))
    contas = [dict(l) for l in con.execute("select conta, banco, titular, doc_extrato from contas order by conta")]
    return {"relacionamentos": len(registros), "contas": contas, "avisos": avisos}


# ---------- consultas base ----------

def _con(codinome: str):
    return db.abrir_caso(caso.caminho(codinome))


def _where(conta=None, inicio=None, fim=None, doc=None, contraparte=None) -> tuple[str, list]:
    cond, args = [], []
    if conta:
        cond.append("t.conta=?"); args.append(conta)
    if inicio:
        cond.append("t.data>=?"); args.append(inicio)
    if fim:
        cond.append("t.data<=?"); args.append(fim)
    if doc:
        cond.append("t.doc_id=?"); args.append(doc)
    if contraparte:
        cond.append("(t.contraparte=? or t.contraparte_conta=?)"); args += [contraparte, contraparte]
    return (" where " + " and ".join(cond)) if cond else "", args


def _txs(con, **filtros) -> list[dict]:
    w, args = _where(**filtros)
    linhas = [dict(l) for l in con.execute(f"select t.* from transacoes t{w} order by t.conta, t.data, t.tx_id", args)]
    if not linhas:
        raise caso.ErroCaso("nenhum lançamento em caso.db para os filtros dados; rode `banco importar <COD> DOC-###`")
    for l in linhas:
        l["ponteiro"] = util.ponteiro(l["doc_id"], f"tx#{l['tx_id']}")
    return linhas


def _contas(con) -> dict[str, dict]:
    return {l["conta"]: dict(l) for l in con.execute("select * from contas")}


def _titular(contas: dict, conta: str | None) -> str | None:
    c = contas.get(conta) if conta else None
    return c["titular"] if c and c["titular"] else None


def _docs(txs: list[dict]) -> list[str]:
    return sorted({t["doc_id"] for t in txs})


def _por_conta(txs: list[dict]) -> dict[str, list[dict]]:
    g = collections.defaultdict(list)
    for t in txs:
        g[t["conta"]].append(t)
    return g


def _tx_saida(t: dict) -> dict:
    return {
        "ponteiro": t["ponteiro"], "conta": t["conta"], "data": t["data"], "historico": t["historico"],
        "valor": util.reais(t["valor_centavos"]), "natureza": t["natureza"], "saldo": util.reais(t["saldo_centavos"]),
        "contraparte": t["contraparte"], "contraparte_conta": t["contraparte_conta"], "local": t["local"],
    }


def _casa(padroes, texto: str | None) -> bool:
    return bool(texto) and layouts.primeiro(padroes, texto) is not None


# ---------- comandos ----------

def lancamentos(codinome: str, conta=None, inicio=None, fim=None, doc=None, contraparte=None, limite: int | None = 50) -> dict:
    con = _con(codinome)
    txs = _txs(con, conta=conta, inicio=inicio, fim=fim, doc=doc, contraparte=contraparte)
    con.close()
    txs.sort(key=lambda t: (t["data"], t["doc_id"], t["tx_id"]))
    total = len(txs)
    if limite:
        txs = txs[:limite]
    return {"codinome": codinome, "total": total, "exibidos": len(txs), "docs": _docs(txs), "lancamentos": [_tx_saida(t) for t in txs]}


def integridade(codinome: str, lacuna_dias: int = 30, conta=None, doc=None) -> dict:
    con = _con(codinome)
    txs = _txs(con, conta=conta, doc=doc)
    contas = _contas(con)
    ccs = [dict(l) for l in con.execute("select * from relacionamentos_ccs")]
    con.close()
    lay = layouts.carregar("simba")
    esperado = lay.get("sem_contraparte_esperado", [])
    saida_contas, duplicidades = [], []
    for cta, lista in _por_conta(txs).items():
        lista.sort(key=lambda t: (t["data"], t["tx_id"]))
        lacunas = []
        for a, b in zip(lista, lista[1:]):
            dias = util.dias_entre(a["data"], b["data"])
            if dias > lacuna_dias:
                lacunas.append({"de": a["data"], "ate": b["data"], "dias": dias, "apos": a["ponteiro"], "antes": b["ponteiro"]})
        divergencias, primeira = 0, None
        anterior = lista[0]["saldo_centavos"]
        for t in lista[1:]:
            if anterior is None or t["saldo_centavos"] is None:
                anterior = t["saldo_centavos"]
                continue
            esperado_saldo = anterior + (t["valor_centavos"] if t["natureza"] == "C" else -t["valor_centavos"])
            if esperado_saldo != t["saldo_centavos"]:
                divergencias += 1
                primeira = primeira or {"ponteiro": t["ponteiro"], "data": t["data"], "saldo_informado": util.reais(t["saldo_centavos"]),
                                        "saldo_reconstruido": util.reais(esperado_saldo), "diferenca": util.reais(t["saldo_centavos"] - esperado_saldo)}
            anterior = t["saldo_centavos"]
        com_saldo = sum(1 for t in lista if t["saldo_centavos"] is not None)
        sem_cp = [t for t in lista if not t["contraparte"] and not t["contraparte_conta"]]
        sem_cp_anomalo = [t for t in sem_cp if not _casa(esperado, t["historico"])]
        chaves = collections.Counter((t["data"], t["valor_centavos"], t["natureza"], t["documento"]) for t in lista)
        dup = [{"conta": cta, "data": k[0], "valor": util.reais(k[1]), "natureza": k[2], "documento": k[3], "ocorrencias": v,
                "ponteiros": [t["ponteiro"] for t in lista if (t["data"], t["valor_centavos"], t["natureza"], t["documento"]) == k]}
               for k, v in chaves.items() if v > 1 and k[3]]
        duplicidades += dup
        saida_contas.append({
            "conta": cta, "titular": _titular(contas, cta), "banco": lista[0]["banco"], "docs": _docs(lista),
            "periodo_inicio": lista[0]["data"], "periodo_fim": lista[-1]["data"], "lancamentos": len(lista),
            "lacunas": lacunas,
            "saldo": {"lancamentos_com_saldo": com_saldo, "divergencias": divergencias, "conferido": com_saldo == len(lista) and divergencias == 0,
                      "primeira_divergencia": primeira},
            "sem_contraparte": {"n": len(sem_cp), "pct": round(100 * len(sem_cp) / len(lista), 1),
                                "n_anomalo": len(sem_cp_anomalo), "pct_anomalo": round(100 * len(sem_cp_anomalo) / len(lista), 1),
                                "ponteiros_anomalos": [t["ponteiro"] for t in sem_cp_anomalo][:20]},
            "duplicidades": len(dup),
        })
    com_extrato = {c["conta"] for c in saida_contas} | {c for c, v in contas.items() if v["doc_extrato"]}
    ccs_sem = sorted({(r["conta"], r["pessoa"], r["banco"]) for r in ccs if r["conta"] not in com_extrato and r["tipo"] == "TITULAR"})
    return {
        "codinome": codinome, "parametros": {"lacuna_dias": lacuna_dias}, "docs": _docs(txs),
        "contas": saida_contas,
        "contas_ccs_sem_extrato": [{"conta": c, "titular": p, "banco": b} for c, p, b in ccs_sem],
        "duplicidades": duplicidades,
        "total_lacunas": sum(len(c["lacunas"]) for c in saida_contas),
        "total_divergencias_saldo": sum(c["saldo"]["divergencias"] for c in saida_contas),
    }


def resumo(codinome: str, conta=None, inicio=None, fim=None, doc=None) -> dict:
    con = _con(codinome)
    txs = _txs(con, conta=conta, inicio=inicio, fim=fim, doc=doc)
    contas = _contas(con)
    con.close()
    saida = []
    for cta, lista in _por_conta(txs).items():
        lista.sort(key=lambda t: (t["data"], t["tx_id"]))
        cred = [t for t in lista if t["natureza"] == "C"]
        deb = [t for t in lista if t["natureza"] == "D"]
        mc = max(cred, key=lambda t: t["valor_centavos"], default=None)
        md = max(deb, key=lambda t: t["valor_centavos"], default=None)
        saida.append({
            "conta": cta, "titular": _titular(contas, cta), "banco": lista[0]["banco"], "docs": _docs(lista),
            "periodo_inicio": lista[0]["data"], "periodo_fim": lista[-1]["data"], "lancamentos": len(lista),
            "n_creditos": len(cred), "creditos": util.reais(sum(t["valor_centavos"] for t in cred)),
            "n_debitos": len(deb), "debitos": util.reais(sum(t["valor_centavos"] for t in deb)),
            "maior_credito": {"valor": util.reais(mc["valor_centavos"]), "data": mc["data"], "contraparte": mc["contraparte"], "ponteiro": mc["ponteiro"]} if mc else None,
            "maior_debito": {"valor": util.reais(md["valor_centavos"]), "data": md["data"], "contraparte": md["contraparte"], "ponteiro": md["ponteiro"]} if md else None,
            "saldo_inicial": util.reais(lista[0]["saldo_centavos"]), "saldo_final": util.reais(lista[-1]["saldo_centavos"]),
            "sem_contraparte": sum(1 for t in lista if not t["contraparte"] and not t["contraparte_conta"]),
        })
    return {"codinome": codinome, "docs": _docs(txs), "contas": saida,
            "total_creditos": util.reais(sum(t["valor_centavos"] for t in txs if t["natureza"] == "C")),
            "total_debitos": util.reais(sum(t["valor_centavos"] for t in txs if t["natureza"] == "D"))}


def contrapartes(codinome: str, top: int = 20, conta=None, inicio=None, fim=None, doc=None) -> dict:
    con = _con(codinome)
    txs = _txs(con, conta=conta, inicio=inicio, fim=fim, doc=doc)
    contas = _contas(con)
    con.close()
    saida = []
    for cta, lista in _por_conta(txs).items():
        agg: dict[str, dict] = {}
        for t in lista:
            chave = t["contraparte"] or t["contraparte_conta"]
            if not chave:
                continue
            a = agg.setdefault(chave, {"contraparte": chave, "contas_contraparte": set(), "n": 0, "creditos": 0, "debitos": 0,
                                       "primeira": t["data"], "ultima": t["data"], "ponteiros": []})
            if t["contraparte_conta"]:
                a["contas_contraparte"].add(t["contraparte_conta"])
            a["n"] += 1
            a["creditos" if t["natureza"] == "C" else "debitos"] += t["valor_centavos"]
            a["primeira"], a["ultima"] = min(a["primeira"], t["data"]), max(a["ultima"], t["data"])
            a["ponteiros"].append(t["ponteiro"])
        lista_agg = sorted(agg.values(), key=lambda a: -(a["creditos"] + a["debitos"]))[:top]
        saida.append({
            "conta": cta, "titular": _titular(contas, cta), "contrapartes_distintas": len(agg),
            "contrapartes": [{**a, "contas_contraparte": sorted(a["contas_contraparte"]), "creditos": util.reais(a["creditos"]),
                              "debitos": util.reais(a["debitos"]), "total": util.reais(a["creditos"] + a["debitos"]),
                              "titular_conhecido": _titular(contas, next(iter(a["contas_contraparte"]), None))} for a in lista_agg],
        })
    return {"codinome": codinome, "parametros": {"top": top}, "docs": _docs(txs), "contas": saida}


def especie(codinome: str, conta=None, inicio=None, fim=None, doc=None) -> dict:
    con = _con(codinome)
    txs = _txs(con, conta=conta, inicio=inicio, fim=fim, doc=doc)
    contas = _contas(con)
    con.close()
    padroes = layouts.carregar("simba").get("especie", [])
    ops = [t for t in txs if _casa(padroes, t["historico"])]
    saida = []
    for cta, lista in _por_conta(ops).items():
        dep = [t for t in lista if t["natureza"] == "C"]
        saq = [t for t in lista if t["natureza"] == "D"]
        locais = collections.Counter((t["local"] or "não informado") for t in lista)
        saida.append({
            "conta": cta, "titular": _titular(contas, cta), "operacoes": len(lista),
            "depositos": {"n": len(dep), "total": util.reais(sum(t["valor_centavos"] for t in dep))},
            "saques": {"n": len(saq), "total": util.reais(sum(t["valor_centavos"] for t in saq))},
            "por_local": [{"local": k, "n": v} for k, v in locais.most_common()],
            "lancamentos": [_tx_saida(t) for t in lista],
        })
    return {"codinome": codinome, "docs": _docs(txs), "operacoes_especie": len(ops),
            "total_depositos": util.reais(sum(t["valor_centavos"] for t in ops if t["natureza"] == "C")),
            "total_saques": util.reais(sum(t["valor_centavos"] for t in ops if t["natureza"] == "D")), "contas": saida}


def _janela(data: str, janela: str) -> str:
    if janela == "semana":
        d = dt.date.fromisoformat(data).isocalendar()
        return f"{d.year}-W{d.week:02d}"
    return data


def fracionamento(codinome: str, limiar: float = 10000.0, janela: str = "dia", minimo: int = 2, conta=None, inicio=None, fim=None, doc=None) -> dict:
    """Grupos de >= `minimo` operações, cada uma abaixo de `limiar`, na mesma conta/natureza/janela, somando >= limiar."""
    con = _con(codinome)
    txs = _txs(con, conta=conta, inicio=inicio, fim=fim, doc=doc)
    contas = _contas(con)
    con.close()
    limiar_c = round(limiar * 100)
    padroes = layouts.carregar("simba").get("especie", [])
    grupos = collections.defaultdict(list)
    for t in txs:
        if t["valor_centavos"] < limiar_c:
            grupos[(t["conta"], t["natureza"], _janela(t["data"], janela))].append(t)
    saida = []
    for (cta, nat, jan), lista in grupos.items():
        soma = sum(t["valor_centavos"] for t in lista)
        if len(lista) >= minimo and soma >= limiar_c:
            saida.append({
                "conta": cta, "titular": _titular(contas, cta), "natureza": nat, "janela": jan, "operacoes": len(lista),
                "soma": util.reais(soma), "valores": [util.reais(t["valor_centavos"]) for t in lista],
                "especie": all(_casa(padroes, t["historico"]) for t in lista),
                "contrapartes": sorted({t["contraparte"] for t in lista if t["contraparte"]}),
                "ponteiros": [t["ponteiro"] for t in lista],
            })
    saida.sort(key=lambda g: (-g["soma"], g["conta"], g["janela"]))
    return {"codinome": codinome, "parametros": {"limiar": limiar, "janela": janela, "minimo": minimo}, "docs": _docs(txs),
            "grupos_suspeitos": len(saida), "grupos": saida}


def passagem(codinome: str, horas: int = 48, conta=None, inicio=None, fim=None, doc=None) -> dict:
    """Índice de passagem: fração dos créditos que sai (FIFO) em até `horas` horas. Giro = créditos / saldo médio."""
    con = _con(codinome)
    txs = _txs(con, conta=conta, inicio=inicio, fim=fim, doc=doc)
    contas = _contas(con)
    con.close()
    saida = []
    for cta, lista in _por_conta(txs).items():
        lista.sort(key=lambda t: (t["data"], t["tx_id"]))
        creditos = sum(t["valor_centavos"] for t in lista if t["natureza"] == "C")
        debitos = sum(t["valor_centavos"] for t in lista if t["natureza"] == "D")
        fila: collections.deque = collections.deque()
        casado, casados_ptr = 0, []
        for t in lista:
            if t["natureza"] == "C":
                fila.append([t["data"], t["valor_centavos"], t["ponteiro"]])
                continue
            restante = t["valor_centavos"]
            while fila and restante > 0:
                data_c, saldo_c, ptr_c = fila[0]
                if util.dias_entre(data_c, t["data"]) * 24 > horas:
                    fila.popleft()      # crédito antigo demais: não conta como passagem
                    continue
                usa = min(saldo_c, restante)
                casado += usa
                restante -= usa
                fila[0][1] -= usa
                if usa:
                    casados_ptr.append({"credito": ptr_c, "debito": t["ponteiro"], "valor": util.reais(usa)})
                if fila[0][1] == 0:
                    fila.popleft()
        saldos = [t["saldo_centavos"] for t in lista if t["saldo_centavos"] is not None]
        saldo_medio = statistics.fmean(saldos) if saldos else None
        indice = round(casado / creditos, 3) if creditos else None
        saida.append({
            "conta": cta, "titular": _titular(contas, cta), "lancamentos": len(lista),
            "creditos": util.reais(creditos), "debitos": util.reais(debitos), "creditos_casados": util.reais(casado),
            "indice_passagem": indice,
            "saldo_medio": util.reais(round(saldo_medio)) if saldo_medio is not None else None,
            "giro": round(creditos / saldo_medio, 2) if saldo_medio else None,
            "perfil_passagem": bool(indice is not None and indice >= 0.8),
            "pares": casados_ptr[:50],
        })
    saida.sort(key=lambda c: -(c["indice_passagem"] or 0))
    return {"codinome": codinome, "parametros": {"horas": horas, "limiar_perfil": 0.8}, "docs": _docs(txs), "contas": saida}


def _arestas(txs: list[dict], contas: dict, nivel: str) -> dict[tuple[str, str], list[dict]]:
    """Arestas de transferência (origem, destino) -> lançamentos, com espelhos unificados por (data, valor)."""
    def no(conta: str | None, pessoa: str | None) -> str | None:
        if nivel == "entidade":
            return _titular(contas, conta) or pessoa or conta
        return conta or pessoa

    arestas: dict[tuple[str, str], dict[tuple, dict]] = collections.defaultdict(dict)
    for t in txs:
        proprio = no(t["conta"], _titular(contas, t["conta"]))
        outro = no(t["contraparte_conta"], t["contraparte"])
        if not outro or outro == proprio:
            continue
        chave = (proprio, outro) if t["natureza"] == "D" else (outro, proprio)
        marca = (t["data"], t["valor_centavos"])
        registro = arestas[chave].setdefault(marca, {"data": t["data"], "valor": util.reais(t["valor_centavos"]), "ponteiros": [], "espelhada": False})
        if registro["ponteiros"]:
            registro["espelhada"] = True
        registro["ponteiros"].append(t["ponteiro"])
    return {k: sorted(v.values(), key=lambda r: r["data"]) for k, v in arestas.items()}


def circularidade(codinome: str, nivel: str = "conta", max_comprimento: int = 6, incluir_terceiros: bool = False,
                  inicio=None, fim=None, doc=None) -> dict:
    """Ciclos A->B->...->A no grafo de transferências. Por padrão só entre contas (ou titulares) com extrato no caso;
    com incluir_terceiros, contrapartes externas também podem ser nós intermediários."""
    con = _con(codinome)
    txs = _txs(con, inicio=inicio, fim=fim, doc=doc)
    contas = _contas(con)
    con.close()
    arestas = _arestas(txs, contas, nivel)
    if not incluir_terceiros:
        do_caso = {c for c, v in contas.items() if v["doc_extrato"]}
        if nivel == "entidade":
            do_caso = {_titular(contas, c) or c for c in do_caso}
        arestas = {k: v for k, v in arestas.items() if k[0] in do_caso and k[1] in do_caso}
    g = nx.DiGraph()
    for (a, b), regs in arestas.items():
        g.add_edge(a, b, n=len(regs), total=sum(round(r["valor"] * 100) for r in regs))
    ciclos = []
    for ciclo in nx.simple_cycles(g, length_bound=max_comprimento):
        if len(ciclo) < 2:
            continue
        passos = []
        for i, a in enumerate(ciclo):
            b = ciclo[(i + 1) % len(ciclo)]
            regs = arestas[(a, b)]
            passos.append({"de": a, "para": b, "transferencias": len(regs), "total": util.reais(sum(round(r["valor"] * 100) for r in regs)),
                           "primeira": regs[0]["data"], "ultima": regs[-1]["data"], "ponteiros": [p for r in regs for p in r["ponteiros"]][:10]})
        # ordem temporal coerente: existe rotação em que as primeiras datas não decrescem
        datas = [p["primeira"] for p in passos]
        coerente = any(all(datas[(k + i) % len(datas)] <= datas[(k + i + 1) % len(datas)] for i in range(len(datas) - 1)) for k in range(len(datas)))
        ciclos.append({"nos": ciclo, "comprimento": len(ciclo), "valor_minimo_no_ciclo": min(p["total"] for p in passos),
                       "ordem_temporal_coerente": coerente, "passos": passos})
    ciclos.sort(key=lambda c: (-c["valor_minimo_no_ciclo"], c["comprimento"]))
    return {"codinome": codinome, "parametros": {"nivel": nivel, "max_comprimento": max_comprimento, "incluir_terceiros": incluir_terceiros},
            "docs": _docs(txs), "nos": g.number_of_nodes(), "arestas": g.number_of_edges(), "ciclos": len(ciclos), "lista": ciclos}


def cruzar_alvos(codinome: str, fonte: str | None = None, inicio=None, fim=None, doc=None) -> dict:
    """Transferências diretas entre alvos: titulares/contas do caso e, com --fonte rif, envolvidos do RIF."""
    con = _con(codinome)
    txs = _txs(con, inicio=inicio, fim=fim, doc=doc)
    contas = _contas(con)
    alvos = {c for c, v in contas.items() if v["doc_extrato"]} | {v["titular"] for v in contas.values() if v["titular"]}
    origem_alvos = {"contas_do_caso": sorted(alvos)}
    if fonte and fonte.lower() == "rif":
        rif_tokens = set()
        for l in con.execute("select titular, envolvidos from comunicacoes_rif"):
            if l["titular"]:
                rif_tokens.add(l["titular"])
            rif_tokens |= {e["pseudonimo"] for e in json.loads(l["envolvidos"])}
        if not rif_tokens:
            raise caso.ErroCaso("--fonte rif: nenhuma comunicação parseada (rode `rif parse`)")
        origem_alvos["rif"] = sorted(rif_tokens)
        alvos |= rif_tokens
    con.close()
    diretas = [t for t in txs if (t["contraparte"] in alvos) or (t["contraparte_conta"] in alvos)]
    pares: dict[tuple[str, str], dict] = {}
    for t in diretas:
        proprio = _titular(contas, t["conta"]) or t["conta"]
        outro = _titular(contas, t["contraparte_conta"]) or t["contraparte"] or t["contraparte_conta"]
        de, para = (proprio, outro) if t["natureza"] == "D" else (outro, proprio)
        p = pares.setdefault((de, para), {"de": de, "para": para, "n": 0, "total": 0, "primeira": t["data"], "ultima": t["data"], "ponteiros": [], "_marcas": collections.Counter()})
        p["n"] += 1
        p["total"] += t["valor_centavos"]
        p["primeira"], p["ultima"] = min(p["primeira"], t["data"]), max(p["ultima"], t["data"])
        p["ponteiros"].append(t["ponteiro"])
        p["_marcas"][(t["data"], t["valor_centavos"])] += 1
    lista = []
    for p in pares.values():
        espelhadas = sum(1 for v in p.pop("_marcas").values() if v > 1)
        lista.append({**p, "total": util.reais(p["total"]), "lancamentos_espelhados": espelhadas,
                      "n_transferencias_unicas": p["n"] - espelhadas})
    lista.sort(key=lambda p: -p["total"])
    return {"codinome": codinome, "parametros": {"fonte": fonte}, "docs": _docs(txs), "alvos": origem_alvos,
            "lancamentos_entre_alvos": len(diretas), "pares": lista}


def linha_tempo(codinome: str, granularidade: str = "mes", conta=None, inicio=None, fim=None, doc=None) -> dict:
    con = _con(codinome)
    txs = _txs(con, conta=conta, inicio=inicio, fim=fim, doc=doc)
    con.close()

    def periodo(data: str) -> str:
        if granularidade == "dia":
            return data
        if granularidade == "semana":
            return _janela(data, "semana")
        return data[:7]

    serie: dict[str, dict] = {}
    for t in txs:
        s = serie.setdefault(periodo(t["data"]), {"periodo": periodo(t["data"]), "lancamentos": 0, "creditos": 0, "debitos": 0, "contas": set()})
        s["lancamentos"] += 1
        s["creditos" if t["natureza"] == "C" else "debitos"] += t["valor_centavos"]
        s["contas"].add(t["conta"])
    pontos = sorted(serie.values(), key=lambda s: s["periodo"])
    totais = [s["creditos"] + s["debitos"] for s in pontos]
    media = statistics.fmean(totais) if totais else 0
    desvio = statistics.pstdev(totais) if len(totais) > 1 else 0
    limiar = media + 1.5 * desvio
    saida = [{"periodo": s["periodo"], "lancamentos": s["lancamentos"], "creditos": util.reais(s["creditos"]), "debitos": util.reais(s["debitos"]),
              "movimentacao": util.reais(s["creditos"] + s["debitos"]), "contas": len(s["contas"]),
              "pico": len(totais) >= 3 and (s["creditos"] + s["debitos"]) > limiar} for s in pontos]
    return {"codinome": codinome, "parametros": {"granularidade": granularidade, "criterio_pico": "movimentacao > media + 1,5 desvio-padrao"},
            "docs": _docs(txs), "media_movimentacao": util.reais(round(media)), "picos": [s["periodo"] for s in saida if s["pico"]], "serie": saida}
