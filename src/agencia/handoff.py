"""F5 — `handoff`: pacote para os pipelines `representacao-policial-drcc` e `relatorio-final-drcc`.

Gera 04_produtos/handoff.json (pseudonimizado) validado contra schemas/handoff.schema.json.
Com --reidentificar, grava também 04_produtos/render/handoff.json com as identidades reais (fora do modelo).
O schema local é PROVISÓRIO até o Fabbro entregar o handoff_schema.json da camada comum/; a
correspondência de campos fica em `MAPA_COMUM` para facilitar a adaptação.
"""
import collections
import json
from pathlib import Path

import jsonschema

from agencia import achados, caso, cofre, custodia, db, grafo, util

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "handoff.schema.json"
VERSAO = "0.1-provisorio"

# campo do handoff -> campo esperado pela camada comum (a confirmar com o handoff_schema.json real)
MAPA_COMUM = {
    "caso.codinome": "operacao",
    "envolvidos[].pseudonimo": "envolvidos[].id",
    "envolvidos[].papeis": "envolvidos[].papel",
    "achados[].enunciado": "fatos[].texto",
    "achados[].fontes": "fatos[].fontes",
    "diligencias[]": "medidas_sugeridas[]",
}


def _produto_atual(d: Path) -> dict | None:
    produtos = sorted((d / "04_produtos").glob("informacao_analise_v*.md"), key=lambda p: int("".join(filter(str.isdigit, p.stem)) or 0))
    if not produtos:
        return None
    p = produtos[-1]
    return {"arquivo": f"04_produtos/{p.name}", "sha256": custodia.sha256_arquivo(p), "versao": int("".join(filter(str.isdigit, p.stem)) or 0)}


def montar(codinome: str) -> dict:
    d = caso.caminho(codinome)
    estado = caso.estado(codinome)
    con = db.abrir_caso(d)
    docs = [dict(l) for l in con.execute("select doc_id, nome, tipo, sha256, tamanho, recebido_em, paginas, linhas from documentos order by doc_id")]
    contas = {l["conta"]: dict(l) for l in con.execute("select * from contas")}
    periodo_docs = {l["doc_id"]: (l["ini"], l["fim"]) for l in con.execute("select doc_id, min(data) ini, max(data) fim from transacoes group by doc_id")}
    for l in con.execute("select doc_id, periodo_inicio, periodo_fim from rif_cabecalho"):
        periodo_docs[l["doc_id"]] = (l["periodo_inicio"], l["periodo_fim"])
    for doc in docs:
        doc["periodo_inicio"], doc["periodo_fim"] = periodo_docs.get(doc["doc_id"], (None, None))
    vinculos = [dict(l) for l in con.execute("select * from vinculos")]
    con.close()

    lista_achados = grafo._achados(d)
    envolvidos: dict[str, dict] = {}

    def env(tok: str) -> dict:
        return envolvidos.setdefault(tok, {"pseudonimo": tok, "tipo": tok.split("-")[0], "papeis": set(), "contas": set(), "fontes": set(),
                                           "achados": [], "relevancia_maxima": None})

    ordem = {"alta": 0, "media": 1, "baixa": 2}
    for c, v in contas.items():
        if v["titular"]:
            e = env(v["titular"])
            e["contas"].add(c)
            e["papeis"].add("titular_de_conta")
            e["fontes"].add("CCS" if v["origem"] == "CCS" else "SIMBA")
    for v in vinculos:
        for tok in (v["origem"], v["destino"]):
            if tok.split("-")[0] in ("PF", "PJ"):
                env(tok)["fontes"].add({"transferencia": "SIMBA", "rif": "RIF", "ccs": "CCS", "achado": "ACHADO"}[v["tipo"]])
        if v["tipo"] == "rif":
            env(v["origem"])["papeis"].add("titular_rif")
            for p in json.loads(v["papeis"]):
                env(v["destino"])["papeis"].add(f"{p}_rif")
    for agente, a in lista_achados:
        for tok in a.get("entidades") or []:
            e = env(tok)
            e["achados"].append(f"{agente}:{a.get('id')}")
            rel = a.get("relevancia")
            if rel and (e["relevancia_maxima"] is None or ordem.get(rel, 2) < ordem[e["relevancia_maxima"]]):
                e["relevancia_maxima"] = rel

    achados_saida = [{
        "id": a.get("id"), "agente": agente, "rotulo": a.get("rotulo"), "relevancia": a.get("relevancia"), "enunciado": a.get("enunciado"),
        "raciocinio": a.get("raciocinio"), "entidades": a.get("entidades") or [],
        "valores": [{"valor": v.get("valor"), "moeda": v.get("moeda", "BRL"), "origem": v.get("origem")} for v in a.get("valores") or [] if isinstance(v, dict)],
        "periodo": a.get("periodo"),
        "fontes": [util.ponteiro(f["doc_id"], f["localizador"]) for f in a.get("fontes") or [] if isinstance(f, dict) and "doc_id" in f],
        "diligencia": a.get("diligencia"),
    } for agente, a in lista_achados]
    achados_saida.sort(key=lambda a: (ordem.get(a["relevancia"], 3), a["agente"], a["id"] or ""))

    try:
        dil = achados.diligencias(codinome)["por_tipo"]
    except caso.ErroCaso:
        dil = []
    diligencias = [{"tipo": g["tipo"], "alvo": x["alvo"], "motivos": x["motivos"], "relevancia": x["relevancia"], "achados": x["achados"]} for g in dil for x in g["diligencias"]]

    convergentes = []
    try:
        cen = grafo.centrais(codinome, top=50)
        convergentes = [{"pseudonimo": x["no"], "fontes": x["fontes"], "grau": x["grau"], "ponte": x["ponte"]} for x in cen["centrais"] if x["convergencia"]]
    except caso.ErroCaso:
        pass

    return {
        "versao_handoff": VERSAO,
        "gerado_em": caso.agora(),
        "caso": {"codinome": codinome, "fase": estado["fase"], "agentes_concluidos": estado.get("agentes_concluidos", []),
                 "alertas": estado.get("alertas", []), "pendencias": estado.get("pendencias", [])},
        "produto": _produto_atual(d),
        "documentos": docs,
        "envolvidos": sorted(
            [{**e, "papeis": sorted(e["papeis"]), "contas": sorted(e["contas"]), "fontes": sorted(e["fontes"]), "n_achados": len(e["achados"])}
             for e in envolvidos.values()],
            key=lambda e: (ordem.get(e["relevancia_maxima"] or "", 3), -e["n_achados"], e["pseudonimo"])),
        "achados": achados_saida,
        "convergencias": convergentes,
        "diligencias": diligencias,
        "resumo": {"documentos": len(docs), "por_tipo": dict(collections.Counter(x["tipo"] for x in docs)), "envolvidos": len(envolvidos),
                   "achados": len(achados_saida), "por_rotulo": dict(collections.Counter(a["rotulo"] for a in achados_saida)),
                   "diligencias": len(diligencias), "convergencias": len(convergentes)},
        "mapa_camada_comum": MAPA_COMUM,
    }


def validar(pacote: dict) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return [f"{'/'.join(str(p) for p in e.path) or '(raiz)'}: {e.message}" for e in sorted(v.iter_errors(pacote), key=lambda e: list(e.path))]


def handoff(codinome: str, reidentificar: bool = False) -> dict:
    d = caso.caminho(codinome)
    pacote = montar(codinome)
    erros = validar(pacote)
    if erros:
        raise caso.ErroCaso("handoff inválido contra schemas/handoff.schema.json: " + "; ".join(erros[:5]))
    texto = json.dumps(pacote, ensure_ascii=False, indent=2)
    destino = d / "04_produtos" / "handoff.json"
    destino.write_text(texto, encoding="utf-8")
    saida = {"codinome": codinome, "arquivo": "04_produtos/handoff.json", "sha256": custodia.sha256_texto(texto), "resumo": pacote["resumo"], "versao_handoff": VERSAO}
    if reidentificar:
        cf = cofre.Cofre(d / "_cofre" / "identidades.db")
        reid, subst, rest = cf.reidentificar_contando(texto)
        pasta = d / "04_produtos" / "render"
        pasta.mkdir(exist_ok=True)
        (pasta / "handoff.json").write_text(reid, encoding="utf-8")
        saida["reidentificado"] = {"arquivo": "04_produtos/render/handoff.json", "tokens_substituidos": subst, "tokens_remanescentes": rest}
    return saida
