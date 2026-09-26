"""F6 — Cripto: `cripto importar/fluxos/enderecos/exchanges`.

cripto_movs: um registro por movimentação na exchange (depósito/saque fiat, compra/venda, depósito/saque cripto).
Quantidades de criptoativos ficam como texto decimal e são somadas com Decimal, por ativo — nunca entre ativos.
Rastreamento on-chain fica desligado (sem rede): endereços recorrentes viram diligência.
Ponteiros: [F:DOC-###:mov#N].
"""
import collections
import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation

from agencia import caso, db, layouts, util
from agencia.banco import _tabelas, _cel
from agencia.cofre import sem_acentos

LAYOUT = "cripto"
TIPOS = ("CRIPTO",)
FIAT_TIPOS = ("deposito_fiat", "saque_fiat")


def _tipo(lay: dict, texto: str) -> str | None:
    plano = sem_acentos(texto or "").upper()
    for tipo, padroes in (lay.get("tipos") or {}).items():
        if any(re.search(p, plano) for p in padroes):
            return tipo
    return None


def _quantidade(texto: str) -> str | None:
    s = (texto or "").strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return _dec(Decimal(s))
    except InvalidOperation:
        return None


def _dec(d: Decimal) -> str:
    """Decimal sem notação científica e sem zeros à direita ('2950', '0.0069')."""
    texto = format(d.normalize(), "f")
    return texto if "." not in texto else texto.rstrip("0").rstrip(".") or "0"


def _ts(texto: str, lay: dict) -> str | None:
    s = (texto or "").strip().rstrip("Z")
    for f in lay.get("formato_data") or util.FORMATOS_DATA:
        try:
            return dt.datetime.strptime(s, f).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def importar(codinome: str, doc_id: str, layout: str | None = None) -> dict:
    d = caso.caminho(codinome)
    con = db.abrir_caso(d)
    doc = con.execute("select tipo from documentos where doc_id=?", (doc_id,)).fetchone()
    if doc is None:
        raise caso.ErroCaso(f"{doc_id} não consta de caso.db; rode `ingerir`")
    if doc["tipo"] not in TIPOS and not layout:
        raise caso.ErroCaso(f"{doc_id} é do tipo {doc['tipo']}; `cripto importar` aceita CRIPTO (ou --layout para forçar)")
    lay = layouts.carregar(layout or LAYOUT)
    mapa = lay["movs"]
    sep = lay.get("separador_decimal", ",")
    con.execute("delete from cripto_movs where doc_id=?", (doc_id,))
    avisos, registros = [], []
    mov_id = 0
    for nome_tab, colunas, linhas in _tabelas(d, doc_id):
        idx = layouts.resolver_colunas(mapa, colunas)
        if "tipo" not in idx or "data_hora" not in idx:
            avisos.append(f"tabela {nome_tab}: sem coluna de tipo/data; ignorada (ajuste config/layouts/cripto.yaml)")
            continue
        for n, linha in enumerate(linhas, start=1):
            mov_id += 1
            tipo = _tipo(lay, _cel(linha, idx, "tipo"))
            ts = _ts(_cel(linha, idx, "data_hora"), lay)
            if tipo is None or ts is None:
                avisos.append(f"mov#{mov_id} (tabela {nome_tab}, linha {n}): tipo {_cel(linha, idx, 'tipo')!r} ou data não interpretados")
            exchange = _cel(linha, idx, "exchange") or None
            cliente = util.RE_TOKEN_PESSOA.search(_cel(linha, idx, "cliente") or "")
            conta_cp = util.RE_TOKEN_CONTA.search(_cel(linha, idx, "contraparte_conta") or "") or util.RE_TOKEN_CONTA.search(_cel(linha, idx, "contraparte_agencia") or "")
            ativo = (_cel(linha, idx, "ativo") or "").upper() or None
            registros.append((
                doc_id, mov_id, exchange, cliente.group(0) if cliente else None, _cel(linha, idx, "conta_exchange") or None, ts, tipo, ativo,
                (_cel(linha, idx, "rede") or "").upper() or None, _quantidade(_cel(linha, idx, "quantidade")), util.centavos(_cel(linha, idx, "valor"), sep),
                _cel(linha, idx, "endereco") or None, _cel(linha, idx, "txid") or None, _cel(linha, idx, "contraparte_banco") or None,
                conta_cp.group(0) if conta_cp else None, _cel(linha, idx, "descricao") or None, nome_tab, n,
            ))
    con.executemany("insert into cripto_movs values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", registros)
    con.commit()
    con.close()
    return {"codinome": codinome, "doc_id": doc_id, "movimentacoes": len(registros), "por_tipo": dict(collections.Counter(r[6] or "?" for r in registros)),
            "exchanges": sorted({r[2] for r in registros if r[2]}), "clientes": sorted({r[3] for r in registros if r[3]}),
            "ativos": sorted({r[7] for r in registros if r[7]}), "avisos": avisos}


def _movs(con, cliente=None, doc=None) -> list[dict]:
    cond, args = [], []
    if cliente:
        cond.append("cliente=?"); args.append(cliente)
    if doc:
        cond.append("doc_id=?"); args.append(doc)
    w = (" where " + " and ".join(cond)) if cond else ""
    movs = [dict(l) for l in con.execute(f"select * from cripto_movs{w} order by cliente, ts_utc, mov_id", args)]
    if not movs:
        raise caso.ErroCaso("nenhuma movimentação cripto; rode `cripto importar <COD> DOC-###`")
    for m in movs:
        m["valor"] = util.reais(m.pop("valor_centavos"))
        m["ponteiro"] = util.ponteiro(m["doc_id"], f"mov#{m['mov_id']}")
    return movs


def _soma_qtd(movs) -> str:
    return _dec(sum((Decimal(m["quantidade"]) for m in movs if m["quantidade"]), Decimal(0)))


def fluxos(codinome: str, cliente: str | None = None, doc: str | None = None) -> dict:
    con = db.abrir_caso(caso.caminho(codinome))
    movs = _movs(con, cliente, doc)
    con.close()
    por_conta = collections.defaultdict(list)
    for m in movs:
        por_conta[(m["exchange"] or "?", m["cliente"] or m["conta_exchange"] or "?")].append(m)
    saida = []
    for (ex, cli), lst in sorted(por_conta.items()):
        def bloco(tipo):
            sel = [m for m in lst if m["tipo"] == tipo]
            return {"n": len(sel), "valor_brl": util.reais(sum(round((m["valor"] or 0) * 100) for m in sel)),
                    "contrapartes_bancarias": sorted({m["contraparte_conta"] or m["contraparte_banco"] for m in sel if m["contraparte_conta"] or m["contraparte_banco"]}),
                    "ponteiros": [m["ponteiro"] for m in sel][:20]}
        por_ativo = collections.defaultdict(lambda: collections.defaultdict(list))
        for m in lst:
            if m["tipo"] not in FIAT_TIPOS and m["ativo"]:
                por_ativo[m["ativo"]][m["tipo"]].append(m)
        ativos = [{"ativo": a, **{t: {"n": len(ms), "quantidade": _soma_qtd(ms), "valor_brl": util.reais(sum(round((m["valor"] or 0) * 100) for m in ms)),
                                       "redes": sorted({m["rede"] for m in ms if m["rede"]})} for t, ms in tipos.items()}} for a, tipos in sorted(por_ativo.items())]
        dep, saq = bloco("deposito_fiat"), bloco("saque_fiat")
        saida.append({"exchange": ex, "cliente": cli, "movimentacoes": len(lst), "periodo_utc_inicio": min(m["ts_utc"] for m in lst if m["ts_utc"]),
                      "periodo_utc_fim": max(m["ts_utc"] for m in lst if m["ts_utc"]), "deposito_fiat": dep, "saque_fiat": saq,
                      "saldo_fiat_liquido": util.reais(round(dep["valor_brl"] * 100) - round(saq["valor_brl"] * 100)), "ativos": ativos,
                      "saques_cripto_para_enderecos": sorted({m["endereco"] for m in lst if m["tipo"] == "saque_cripto" and m["endereco"]}),
                      "padrao": "fiat→cripto→saída on-chain" if dep["n"] and any(m["tipo"] == "saque_cripto" for m in lst)
                      else "entrada on-chain→fiat" if saq["n"] and any(m["tipo"] == "deposito_cripto" for m in lst) else None})
    return {"codinome": codinome, "contas": saida, "docs": sorted({m["doc_id"] for m in movs}),
            "total_deposito_fiat": util.reais(sum(round((m["valor"] or 0) * 100) for m in movs if m["tipo"] == "deposito_fiat")),
            "total_saque_fiat": util.reais(sum(round((m["valor"] or 0) * 100) for m in movs if m["tipo"] == "saque_fiat")),
            "nota": "quantidades somadas só dentro do mesmo ativo; valor_brl é o informado pela exchange na data"}


def enderecos(codinome: str, doc: str | None = None) -> dict:
    con = db.abrir_caso(caso.caminho(codinome))
    movs = _movs(con, None, doc)
    con.close()
    agg: dict[str, dict] = {}
    for m in movs:
        if not m["endereco"]:
            continue
        a = agg.setdefault(m["endereco"], {"endereco": m["endereco"], "movimentacoes": 0, "clientes": set(), "exchanges": set(), "ativos": collections.defaultdict(list),
                                          "redes": set(), "primeiro_uso_utc": m["ts_utc"], "ultimo_uso_utc": m["ts_utc"], "tipos": collections.Counter(), "ponteiros": []})
        a["movimentacoes"] += 1
        if m["cliente"]:
            a["clientes"].add(m["cliente"])
        if m["exchange"]:
            a["exchanges"].add(m["exchange"])
        if m["ativo"]:
            a["ativos"][m["ativo"]].append(m)
        if m["rede"]:
            a["redes"].add(m["rede"])
        if m["ts_utc"]:
            a["primeiro_uso_utc"] = min(filter(None, [a["primeiro_uso_utc"], m["ts_utc"]]))
            a["ultimo_uso_utc"] = max(filter(None, [a["ultimo_uso_utc"], m["ts_utc"]]))
        a["tipos"][m["tipo"] or "?"] += 1
        a["ponteiros"].append(m["ponteiro"])
    lista = [{**a, "clientes": sorted(a["clientes"]), "exchanges": sorted(a["exchanges"]), "redes": sorted(a["redes"]), "tipos": dict(a["tipos"]),
              "ativos": [{"ativo": k, "quantidade": _soma_qtd(v), "valor_brl": util.reais(sum(round((m["valor"] or 0) * 100) for m in v))} for k, v in sorted(a["ativos"].items())],
              "recorrente": a["movimentacoes"] > 1, "compartilhado": len(a["clientes"]) > 1} for a in agg.values()]
    lista.sort(key=lambda a: (-len(a["clientes"]), -a["movimentacoes"], a["endereco"]))
    return {"codinome": codinome, "enderecos": len(lista), "compartilhados": [a["endereco"] for a in lista if a["compartilhado"]],
            "recorrentes": [a["endereco"] for a in lista if a["recorrente"]], "lista": lista,
            "diligencias_rastreio": [{"endereco": a["endereco"], "motivo": "endereço externo " + ("compartilhado entre clientes" if a["compartilhado"] else "recorrente"),
                                      "redes": a["redes"], "ponteiros": a["ponteiros"][:5]} for a in lista if a["compartilhado"] or a["recorrente"]],
            "nota": "titularidade de endereço externo não é presumida; rastreio on-chain exige rede e autorização"}


def exchanges(codinome: str, doc: str | None = None, tolerancia_dias: int = 2) -> dict:
    """Exchanges, contas KYC e ligação dos depósitos/saques fiat com as contas bancárias do caso."""
    con = db.abrir_caso(caso.caminho(codinome))
    movs = _movs(con, None, doc)
    contas = {l["conta"]: dict(l) for l in con.execute("select * from contas")}
    txs = [dict(l) for l in con.execute("select doc_id, tx_id, conta, data, valor_centavos, natureza, contraparte, contraparte_conta from transacoes")]
    pjs = {l["pj"] for l in con.execute("select pj from pj")}
    con.close()
    por_ex = collections.defaultdict(lambda: {"clientes": set(), "movimentacoes": 0, "deposito_fiat": 0, "saque_fiat": 0, "docs": set()})
    for m in movs:
        e = por_ex[m["exchange"] or "?"]
        e["movimentacoes"] += 1
        e["docs"].add(m["doc_id"])
        if m["cliente"]:
            e["clientes"].add(m["cliente"])
        if m["tipo"] == "deposito_fiat":
            e["deposito_fiat"] += round((m["valor"] or 0) * 100)
        if m["tipo"] == "saque_fiat":
            e["saque_fiat"] += round((m["valor"] or 0) * 100)
    ligacoes = []
    for m in movs:
        if m["tipo"] not in FIAT_TIPOS:
            continue
        conta = m["contraparte_conta"]
        lig = {"ponteiro": m["ponteiro"], "tipo": m["tipo"], "cliente": m["cliente"], "ts_utc": m["ts_utc"], "valor": m["valor"], "conta_bancaria": conta,
               "titular_conta": contas.get(conta, {}).get("titular") if conta else None, "conta_do_caso": bool(conta and contas.get(conta, {}).get("doc_extrato")),
               "lancamento_bancario": None, "cliente_e_titular": None}
        if conta and lig["titular_conta"]:
            lig["cliente_e_titular"] = lig["titular_conta"] == m["cliente"]
        data = (m["ts_utc"] or "")[:10]
        cent = round((m["valor"] or 0) * 100)
        if data and cent:
            nat = "D" if m["tipo"] == "deposito_fiat" else "C"
            cand = [t for t in txs if t["valor_centavos"] == cent and t["natureza"] == nat and abs(util.dias_entre(min(t["data"], data), max(t["data"], data))) <= tolerancia_dias
                    and (not conta or t["conta"] == conta)]
            if cand:
                t = cand[0]
                lig["lancamento_bancario"] = {"ponteiro": util.ponteiro(t["doc_id"], f"tx#{t['tx_id']}"), "conta": t["conta"], "data": t["data"], "contraparte": t["contraparte"]}
        ligacoes.append(lig)
    return {"codinome": codinome, "parametros": {"tolerancia_dias": tolerancia_dias},
            "exchanges": [{"exchange": ex, "pj_do_caso": ex in pjs, "clientes": sorted(v["clientes"]), "movimentacoes": v["movimentacoes"],
                           "deposito_fiat": util.reais(v["deposito_fiat"]), "saque_fiat": util.reais(v["saque_fiat"]), "docs": sorted(v["docs"])} for ex, v in sorted(por_ex.items())],
            "ligacoes_fiat": ligacoes, "ligadas_a_lancamento": sum(1 for l in ligacoes if l["lancamento_bancario"]),
            "sem_conta_identificada": sum(1 for l in ligacoes if not l["conta_bancaria"]),
            "diligencias": [{"tipo": "OFICIO_EXCHANGE", "alvo": ex, "motivo": "dados cadastrais (KYC), IPs de acesso, endereços de depósito e saldo atual dos clientes " + ", ".join(sorted(v["clientes"]))}
                            for ex, v in sorted(por_ex.items())]}
