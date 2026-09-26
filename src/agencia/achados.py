"""F4 — Qualidade: `achados validar/verificar/diligencias`.

- validar: cada linha de 03_analises/<agente>/achados.jsonl contra schemas/achado.schema.json, ids únicos,
  agente coerente com a pasta, doc_ids existentes no manifesto.
- verificar: cada ponteiro resolve em caso.db/extraídos; cada valor citado é sustentado por uma fonte
  (lançamento, comunicação, agregado salvo ou texto da página/linhas); entidades existem no caso.
  Com --arquivo, audita a ancoragem de um nota.md/produto: frases factuais sem ponteiro e ponteiros
  que não resolvem.
- diligencias: consolida as diligências dos achados (HIPOTESE/LIMITACAO), sem duplicatas, por tipo.
"""
import collections
import json
import re
from pathlib import Path

import jsonschema

from agencia import agregados, caso, db, util
from agencia.cofre import RE_TOKEN

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "achado.schema.json"
RE_PONTEIRO = re.compile(r"\[F:(DOC-\d{3,}):(p\d+|l\d+(?:-\d+)?|tx#\d+|com#\d+|ccs#\d+|ev#\d+|agg#[a-z0-9_]+)\]")
RE_PAGINA = re.compile(r"^<!-- p(\d+) -->\s*$")
RE_ROTULO = re.compile(r"\b(FATO|INFER[EÊ]NCIA|HIP[OÓ]TESE|LIMITA[CÇ][AÃ]O)\b")
RE_NUMERO = re.compile(r"R\$\s*[\d.]+,\d{2}|\b\d{1,3}(?:\.\d{3})+,\d{2}\b|\b\d+,\d{2}\b|\b\d{2}/\d{2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b")
RE_LINHA_TABELA = re.compile(r"^\s*\|")


def _schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def _agentes(d: Path, agente: str | None) -> list[str]:
    base = d / "03_analises"
    if agente:
        return [agente]
    return sorted(p.parent.name for p in base.glob("*/achados.jsonl") if not p.parent.name.startswith("_"))


def _ler(d: Path, agente: str) -> tuple[list[tuple[int, dict | None, str | None]], Path]:
    """[(nº da linha, achado ou None, erro de JSON ou None)]."""
    arq = d / "03_analises" / agente / "achados.jsonl"
    if not arq.is_file():
        raise caso.ErroCaso(f"{arq.relative_to(d)} não existe")
    saida = []
    for n, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), start=1):
        if not linha.strip():
            continue
        try:
            saida.append((n, json.loads(linha), None))
        except json.JSONDecodeError as e:
            saida.append((n, None, f"JSON inválido: {e.msg} (coluna {e.colno})"))
    return saida, arq


def _manifesto_docs(d: Path) -> dict[str, dict]:
    arq = d / "01_custodia" / "manifesto.json"
    if not arq.is_file():
        return {}
    return {e["doc_id"]: e for e in json.loads(arq.read_text(encoding="utf-8"))}


# ---------- validar ----------

def validar(codinome: str, agente: str | None = None) -> dict:
    d = caso.caminho(codinome)
    validador = jsonschema.Draft202012Validator(_schema(), format_checker=jsonschema.FormatChecker())
    docs = _manifesto_docs(d)
    por_agente, total_erros, total = [], 0, 0
    for ag in _agentes(d, agente):
        linhas, arq = _ler(d, ag)
        erros, ids = [], collections.Counter()
        for n, a, erro_json in linhas:
            if erro_json:
                erros.append({"linha": n, "id": None, "erro": erro_json})
                continue
            total += 1
            for e in sorted(validador.iter_errors(a), key=lambda e: list(e.path)):
                caminho = "/".join(str(p) for p in e.path) or "(raiz)"
                erros.append({"linha": n, "id": a.get("id"), "erro": f"schema: {caminho}: {e.message}"})
            if a.get("id"):
                ids[a["id"]] += 1
            if a.get("agente") and a["agente"] != ag:
                erros.append({"linha": n, "id": a.get("id"), "erro": f"agente {a['agente']!r} difere da pasta {ag!r}"})
            for f in a.get("fontes") or []:
                if isinstance(f, dict) and f.get("doc_id") and docs and f["doc_id"] not in docs:
                    erros.append({"linha": n, "id": a.get("id"), "erro": f"fonte {f['doc_id']} não consta do manifesto"})
            if a.get("rotulo") == "FATO" and a.get("raciocinio") is None and RE_ROTULO.search(a.get("enunciado", "")) and "INFER" in a.get("enunciado", "").upper():
                erros.append({"linha": n, "id": a.get("id"), "erro": "rótulo FATO com enunciado que se declara inferência"})
        for id_, k in ids.items():
            if k > 1:
                erros.append({"linha": None, "id": id_, "erro": f"id repetido {k} vezes"})
        total_erros += len(erros)
        por_agente.append({"agente": ag, "arquivo": str(arq.relative_to(d)), "achados": sum(1 for _, a, _ in linhas if a), "erros": len(erros), "lista": erros})
    if not por_agente:
        raise caso.ErroCaso("nenhum achados.jsonl em 03_analises/*/")
    return {"codinome": codinome, "achados": total, "erros": total_erros, "ok": total_erros == 0, "por_agente": por_agente}


# ---------- resolução de ponteiros ----------

class Resolvedor:
    """Resolve ponteiros contra caso.db e 02_extraido/, e devolve o conteúdo apontado para conferência."""

    def __init__(self, codinome: str):
        self.codinome = codinome
        self.d = caso.caminho(codinome)
        self.con = db.abrir_caso(self.d)
        self.docs = {l["doc_id"]: dict(l) for l in self.con.execute("select * from documentos")}
        self.entidades = {l["pseudonimo"] for l in self.con.execute("select pseudonimo from entidades")}
        self.entidades |= {l["conta"] for l in self.con.execute("select conta from contas")}
        self.entidades |= {l["contraparte"] for l in self.con.execute("select distinct contraparte from transacoes where contraparte is not null")}
        self.entidades |= {l["contraparte_conta"] for l in self.con.execute("select distinct contraparte_conta from transacoes where contraparte_conta is not null")}
        self.titulares = {l["conta"]: l["titular"] for l in self.con.execute("select conta, titular from contas") if l["titular"]}
        self._paginas: dict[str, dict[int, str]] = {}
        self._linhas: dict[str, list[str]] = {}
        self.tabelas = {l["name"] for l in self.con.execute("select name from sqlite_master where type='table'")}

    def fechar(self):
        self.con.close()

    def _extraido(self, doc_id: str):
        if doc_id in self._paginas:
            return
        paginas: dict[int, list[str]] = collections.defaultdict(list)
        linhas: list[str] = []
        arq = self.d / "02_extraido" / f"{doc_id}.md"
        pagina = 1
        if arq.is_file():
            for linha in arq.read_text(encoding="utf-8").split("\n"):
                m = RE_PAGINA.match(linha)
                if m:
                    pagina = int(m.group(1))
                    continue
                paginas[pagina].append(linha)
                linhas.append(linha)
        self._paginas[doc_id] = {p: "\n".join(v) for p, v in paginas.items()}
        self._linhas[doc_id] = linhas

    def resolver(self, doc_id: str, loc: str) -> tuple[bool, dict]:
        """(resolve?, {"tipo", "texto"/"valores"/"datas", "erro"})."""
        if doc_id not in self.docs:
            return False, {"erro": f"{doc_id} não consta de caso.db"}
        if loc.startswith("p"):
            self._extraido(doc_id)
            n = int(loc[1:])
            paginas = self.docs[doc_id]["paginas"]
            if n < 1 or (paginas is not None and n > paginas) or n not in self._paginas[doc_id]:
                return False, {"erro": f"{doc_id} não tem página {n} (páginas={paginas})"}
            return True, {"tipo": "pagina", "texto": self._paginas[doc_id][n]}
        if loc.startswith("l"):
            self._extraido(doc_id)
            a, _, b = loc[1:].partition("-")
            a, b = int(a), int(b or a)
            linhas = self._linhas[doc_id]
            if a < 1 or b > len(linhas) or a > b:
                return False, {"erro": f"{doc_id} tem {len(linhas)} linhas; intervalo {loc} inválido"}
            return True, {"tipo": "linhas", "texto": "\n".join(linhas[a - 1:b])}
        if loc.startswith("tx#"):
            l = self.con.execute("select * from transacoes where doc_id=? and tx_id=?", (doc_id, int(loc[3:]))).fetchone()
            if not l:
                return False, {"erro": f"{doc_id}:{loc} não existe em transacoes (rode `banco importar`?)"}
            ents = {l["conta"], l["contraparte"], l["contraparte_conta"], self.titulares.get(l["conta"]), self.titulares.get(l["contraparte_conta"])} - {None}
            return True, {"tipo": "transacao", "valores": {l["valor_centavos"], l["saldo_centavos"]} - {None}, "datas": {l["data"]}, "entidades": ents}
        if loc.startswith("com#"):
            l = self.con.execute("select * from comunicacoes_rif where doc_id=? and num=?", (doc_id, int(loc[4:]))).fetchone()
            if not l:
                return False, {"erro": f"{doc_id}:{loc} não existe em comunicacoes_rif (rode `rif parse`?)"}
            env = {e["pseudonimo"] for e in json.loads(l["envolvidos"])}
            return True, {"tipo": "comunicacao", "valores": {l["valor_centavos"]} - {None}, "texto": l["texto"] or "",
                          "datas": {l["data_comunicacao"], l["periodo_inicio"], l["periodo_fim"]} - {None}, "entidades": env | ({l["titular"]} - {None})}
        if loc.startswith("ccs#"):
            l = self.con.execute("select * from relacionamentos_ccs where doc_id=? and linha=?", (doc_id, int(loc[4:]))).fetchone()
            if not l:
                return False, {"erro": f"{doc_id}:{loc} não existe em relacionamentos_ccs (rode `banco importar`?)"}
            return True, {"tipo": "ccs", "datas": {l["inicio"], l["fim"]} - {None}, "entidades": {l["pessoa"], l["conta"]} - {None},
                          "texto": f"{l['tipo']} {l['banco']} {l['inicio']} {l['fim']}"}
        if loc.startswith("ev#"):
            if "eventos_telematicos" not in self.tabelas:
                return False, {"erro": f"{doc_id}:{loc}: eventos telemáticos ainda não importados (F6)"}
            l = self.con.execute("select * from eventos_telematicos where doc_id=? and ev_id=?", (doc_id, int(loc[3:]))).fetchone()
            if not l:
                return False, {"erro": f"{doc_id}:{loc} não existe em eventos_telematicos"}
            return True, {"tipo": "evento", "texto": json.dumps(dict(l), ensure_ascii=False, default=str)}
        if loc.startswith("agg#"):
            nome = loc[4:]
            reg = self.con.execute("select docs from agregados where nome=?", (nome,)).fetchone()
            dados = agregados.carregar(self.codinome, nome)
            if not reg or dados is None:
                return False, {"erro": f"agregado {nome!r} não foi salvo (rode a análise com --salvar)"}
            if doc_id not in json.loads(reg["docs"]):
                return False, {"erro": f"agregado {nome!r} não deriva de {doc_id} (docs={json.loads(reg['docs'])})"}
            return True, {"tipo": "agregado", "numeros": _numeros(dados["resultado"]), "texto": json.dumps(dados["resultado"], ensure_ascii=False)}
        return False, {"erro": f"localizador desconhecido: {loc}"}


def _numeros(obj, acc: set | None = None) -> set:
    acc = set() if acc is None else acc
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        acc.add(round(float(obj), 2))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numeros(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _numeros(v, acc)
    return acc


def _formatos_valor(valor: float) -> list[str]:
    c = round(valor * 100)
    inteiro, frac = divmod(abs(c), 100)
    com_milhar = f"{inteiro:,}".replace(",", ".")
    return [f"{com_milhar},{frac:02d}", f"{inteiro},{frac:02d}", f"{inteiro}.{frac:02d}"]


def _valor_sustentado(valor: float, fontes: list[dict]) -> bool:
    c = round(valor * 100)
    for f in fontes:
        if c in f.get("valores", set()):
            return True
        if round(valor, 2) in f.get("numeros", set()):
            return True
        texto = f.get("texto")
        if texto and any(fmt in texto for fmt in _formatos_valor(valor)):
            return True
    return False


# ---------- verificar ----------

def verificar(codinome: str, agente: str | None = None, arquivo: str | None = None) -> dict:
    d = caso.caminho(codinome)
    if arquivo:
        return _verificar_arquivo(codinome, d, arquivo)
    res = Resolvedor(codinome)
    try:
        por_agente, total, total_erros, total_avisos = [], 0, 0, 0
        for ag in _agentes(d, agente):
            linhas, arq = _ler(d, ag)
            erros, avisos = [], []
            for n, a, erro_json in linhas:
                if erro_json or not isinstance(a, dict):
                    erros.append({"linha": n, "id": None, "erro": erro_json or "linha não é um objeto"})
                    continue
                total += 1
                id_ = a.get("id")
                resolvidas = []
                for f in a.get("fontes") or []:
                    if not isinstance(f, dict) or "doc_id" not in f or "localizador" not in f:
                        erros.append({"linha": n, "id": id_, "erro": "fonte sem doc_id/localizador"})
                        continue
                    ok, info = res.resolver(f["doc_id"], f["localizador"])
                    if ok:
                        resolvidas.append(info)
                    else:
                        erros.append({"linha": n, "id": id_, "erro": f"ponteiro [F:{f['doc_id']}:{f['localizador']}] não resolve: {info['erro']}"})
                for v in a.get("valores") or []:
                    valor = v.get("valor") if isinstance(v, dict) else None
                    if valor is None:
                        continue
                    if not resolvidas:
                        erros.append({"linha": n, "id": id_, "erro": f"valor {valor} sem fonte resolvida que o sustente"})
                    elif not _valor_sustentado(float(valor), resolvidas):
                        erros.append({"linha": n, "id": id_, "erro": f"valor {valor} não consta de nenhuma fonte citada ({', '.join(sorted({r['tipo'] for r in resolvidas}))})"})
                for ent in a.get("entidades") or []:
                    if ent not in res.entidades:
                        erros.append({"linha": n, "id": id_, "erro": f"entidade {ent} não existe no caso"})
                citadas = set(re.findall(r"\b(?:PF|PJ|CT|TEL|EML|PIX|END)-\d{4}\b", a.get("enunciado", "")))
                faltam = sorted(citadas - set(a.get("entidades") or []))
                if faltam:
                    avisos.append({"linha": n, "id": id_, "aviso": f"entidades citadas no enunciado e ausentes de `entidades`: {faltam}"})
                ent_fontes = set().union(*(r.get("entidades", set()) for r in resolvidas)) if resolvidas else set()
                if ent_fontes and a.get("entidades"):
                    fora = sorted(set(a["entidades"]) - ent_fontes)
                    if fora and all(r["tipo"] in ("transacao", "comunicacao") for r in resolvidas):
                        avisos.append({"linha": n, "id": id_, "aviso": f"entidades {fora} não aparecem nos lançamentos/comunicações citados"})
                per = a.get("periodo") or {}
                datas = set().union(*(r.get("datas", set()) for r in resolvidas)) if resolvidas else set()
                if per.get("inicio") and per.get("fim") and datas:
                    fora = sorted(x for x in datas if not (per["inicio"] <= x <= per["fim"]))
                    if fora and len(fora) == len(datas):
                        avisos.append({"linha": n, "id": id_, "aviso": f"nenhuma data das fontes ({fora[:3]}) cai no período declarado {per['inicio']}..{per['fim']}"})
                if a.get("rotulo") == "FATO" and any(r["tipo"] == "comunicacao" for r in resolvidas) and not re.search(r"segundo o comunicante|comunicante", a.get("enunciado", ""), re.I):
                    avisos.append({"linha": n, "id": id_, "aviso": "FATO baseado só em RIF sem atribuição ao comunicante"})
            total_erros += len(erros)
            total_avisos += len(avisos)
            por_agente.append({"agente": ag, "arquivo": str(arq.relative_to(d)), "achados": sum(1 for _, a, _ in linhas if a),
                               "erros": len(erros), "avisos": len(avisos), "lista_erros": erros, "lista_avisos": avisos})
        if not por_agente:
            raise caso.ErroCaso("nenhum achados.jsonl em 03_analises/*/")
        return {"codinome": codinome, "achados": total, "erros": total_erros, "avisos": total_avisos, "ok": total_erros == 0, "por_agente": por_agente}
    finally:
        res.fechar()


def _factual(linha: str) -> bool:
    """Linha que afirma algo verificável: tem número/data, token de entidade ou rótulo."""
    return bool(RE_NUMERO.search(linha) or RE_TOKEN.search(linha) or RE_ROTULO.search(linha))


def _verificar_arquivo(codinome: str, d: Path, arquivo: str) -> dict:
    alvo = (d / arquivo).resolve()
    if d.resolve() not in alvo.parents or not alvo.is_file():
        raise caso.ErroCaso(f"arquivo {arquivo!r} não encontrado dentro do caso")
    res = Resolvedor(codinome)
    try:
        sem_ponteiro, nao_resolvem, entidades_desconhecidas = [], [], []
        ponteiros_ok = 0
        em_codigo = False
        cabecalho_tabela = 0
        for n, linha in enumerate(alvo.read_text(encoding="utf-8").split("\n"), start=1):
            s = linha.strip()
            if s.startswith("```"):
                em_codigo = not em_codigo
                continue
            if em_codigo or not s or s.startswith("#") or s.startswith("<!--"):
                cabecalho_tabela = 0
                continue
            if RE_LINHA_TABELA.match(s):
                cabecalho_tabela += 1
                if cabecalho_tabela <= 2:      # cabeçalho e separador da tabela
                    continue
            else:
                cabecalho_tabela = 0
            ptrs = RE_PONTEIRO.findall(linha)
            for doc_id, loc in ptrs:
                ok, info = res.resolver(doc_id, loc)
                if ok:
                    ponteiros_ok += 1
                else:
                    nao_resolvem.append({"linha": n, "ponteiro": f"[F:{doc_id}:{loc}]", "erro": info["erro"]})
            for tok in set(re.findall(r"\b(?:PF|PJ|CT|TEL|EML|PIX|END)-\d{4}\b", linha)):
                if tok not in res.entidades:
                    entidades_desconhecidas.append({"linha": n, "entidade": tok})
            if not ptrs and _factual(s):
                sem_ponteiro.append({"linha": n, "texto": s[:160]})
        erros = len(sem_ponteiro) + len(nao_resolvem) + len(entidades_desconhecidas)
        return {"codinome": codinome, "arquivo": arquivo, "ponteiros_ok": ponteiros_ok, "erros": erros, "ok": erros == 0,
                "frases_sem_ponteiro": sem_ponteiro, "ponteiros_que_nao_resolvem": nao_resolvem, "entidades_desconhecidas": entidades_desconhecidas,
                "nota": "frase factual = contém valor, data, token de entidade ou rótulo; tabelas: cabeçalho e separador são ignorados"}
    finally:
        res.fechar()


# ---------- diligências ----------

def diligencias(codinome: str, agente: str | None = None) -> dict:
    d = caso.caminho(codinome)
    ordem = {"alta": 0, "media": 1, "baixa": 2}
    por_chave: dict[tuple, dict] = {}
    for ag in _agentes(d, agente):
        linhas, _ = _ler(d, ag)
        for _, a, _ in linhas:
            dil = (a or {}).get("diligencia")
            if not dil:
                continue
            chave = (dil.get("tipo") or "OUTRA", dil.get("alvo") or "?")
            item = por_chave.setdefault(chave, {"tipo": chave[0], "alvo": chave[1], "motivos": [], "achados": [], "relevancia": "baixa", "agentes": set()})
            if dil.get("motivo") and dil["motivo"] not in item["motivos"]:
                item["motivos"].append(dil["motivo"])
            item["achados"].append(f"{ag}:{a.get('id')}")
            item["agentes"].add(ag)
            if ordem.get(a.get("relevancia", "baixa"), 2) < ordem[item["relevancia"]]:
                item["relevancia"] = a["relevancia"]
    grupos = collections.defaultdict(list)
    for item in por_chave.values():
        item["agentes"] = sorted(item["agentes"])
        grupos[item["tipo"]].append(item)
    saida = []
    for tipo, itens in sorted(grupos.items()):
        itens.sort(key=lambda i: (ordem[i["relevancia"]], -len(i["achados"]), i["alvo"]))
        saida.append({"tipo": tipo, "diligencias": itens})
    return {"codinome": codinome, "total": len(por_chave), "por_tipo": saida}
