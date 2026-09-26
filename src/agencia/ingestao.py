"""`ingerir`: hash, manifesto, cadeia de custódia, classificação, extração e pseudonimização.

O bruto em 00_brutos/ nunca é alterado. Saídas: 01_custodia/manifesto.json, 01_custodia/cadeia.jsonl,
02_extraido/DOC-###.md, 02_extraido/DOC-###.tabelas/*.csv, caso.db (documentos, entidades).
"""
import collections
import csv
import datetime as dt
import io
import json
import re
from pathlib import Path

from agencia import caso, classificacao, cofre, custodia, db, extracao

TIPOS = classificacao.TIPOS
SUFIXOS = ("_OD", "_ORIGEM", "_DESTINO", "_REMETENTE", "_DESTINATARIO", "_PAGADOR", "_RECEBEDOR", "_FAVORECIDO", "_CONTRAPARTE")
PREVIA_LINHAS = 10
RE_INSTRUCAO = re.compile(
    r"ignore (as|todas as|suas|quaisquer) instru|desconsidere (as|suas) instru|system prompt|"
    r"voce e (um|uma|o|a) (assistente|modelo|ia)\b|execute (o|este|esse) comando|apague (os|todos|tudo)|"
    r"envie (o|este|os) (relatorio|dados|arquivo)s? para"
)


# ---------- manifesto ----------

def _ler_manifesto(d: Path) -> list[dict]:
    arq = d / "01_custodia" / "manifesto.json"
    return json.loads(arq.read_text(encoding="utf-8")) if arq.is_file() else []


def _gravar_manifesto(d: Path, manifesto: list[dict]) -> None:
    arq = d / "01_custodia" / "manifesto.json"
    tmp = arq.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(arq)


# ---------- pseudonimização de tabelas ----------

def _papel_coluna(nome: str) -> tuple[str | None, str]:
    c = cofre.sem_acentos(nome).upper().strip()
    sufixo = next((s for s in SUFIXOS if c.endswith(s)), "")
    base = c[: -len(sufixo)] if sufixo else c
    if "NUMERO_DOCUMENTO" in base or base in ("DOCUMENTO", "NUM_DOCUMENTO"):
        return None, sufixo
    if re.search(r"CPF|CNPJ|NI_|DOCUMENTO_PESSOA", base):
        return "documento", sufixo
    if re.search(r"\bNOME|RAZAO|TITULAR|PESSOA", base):
        return "nome", sufixo
    if re.search(r"AGENCIA", base):
        return "agencia", sufixo
    if re.search(r"CONTA", base) and not re.search(r"TIPO|SITUACAO|MODALIDADE", base):
        return "conta", sufixo
    if re.search(r"TELEFONE|CELULAR|\bFONE", base):
        return "telefone", sufixo
    if re.search(r"E-?MAIL", base):
        return "email", sufixo
    if re.search(r"ENDERECO|LOGRADOURO", base):
        return "endereco", sufixo
    if re.search(r"CHAVE", base):
        return "pix", sufixo
    return None, sufixo


def _anonimizar_tabela(tab: extracao.Tabela, cf: cofre.Cofre) -> extracao.Tabela:
    papeis = [_papel_coluna(c) for c in tab.colunas]
    grupos: dict[str, dict[str, int]] = collections.defaultdict(dict)
    for i, (papel, sufixo) in enumerate(papeis):
        if papel:
            grupos[sufixo].setdefault(papel, i)

    saida = []
    for linha in tab.linhas:
        nova = list(linha)
        tratadas: set[int] = set()
        for g in grupos.values():
            token_doc = None
            if "documento" in g and nova[g["documento"]].strip():
                d = cofre._digitos(nova[g["documento"]])
                tipo = "cpf" if len(d) == 11 else "cnpj" if len(d) == 14 else "pix"
                token_doc = cf.pseudonimo(tipo, nova[g["documento"]])
                nova[g["documento"]] = token_doc
                tratadas.add(g["documento"])
            if "nome" in g and nova[g["nome"]].strip():
                nome = nova[g["nome"]]
                tipo_n = "nome_pj" if (token_doc or "").startswith("PJ") else "nome_pf" if token_doc else cofre.tipo_nome(nome)
                if token_doc:
                    cf.vincular(tipo_n, nome, token_doc)
                nova[g["nome"]] = cf.pseudonimo(tipo_n, nome)
                tratadas.add(g["nome"])
            if "conta" in g and nova[g["conta"]].strip():
                ag = nova[g["agencia"]] if "agencia" in g else ""
                token = cf.pseudonimo("conta", f"{ag}/{nova[g['conta']]}")
                nova[g["conta"]] = token
                tratadas.add(g["conta"])
                if "agencia" in g:
                    nova[g["agencia"]] = token
                    tratadas.add(g["agencia"])
            for papel in ("telefone", "email", "endereco", "pix"):
                if papel in g and nova[g[papel]].strip():
                    nova[g[papel]] = cf.pseudonimo(papel, nova[g[papel]])
                    tratadas.add(g[papel])
        for i, cel in enumerate(nova):
            if i not in tratadas and cel:
                nova[i], _ = cf.anonimizar(cel)
        saida.append(nova)
    return extracao.Tabela(tab.nome, tab.colunas, saida)


# ---------- extraído ----------

def _csv_texto(tab: extracao.Tabela) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(tab.colunas)
    w.writerows(tab.linhas)
    return buf.getvalue()


def _previa_md(tab: extracao.Tabela) -> str:
    esc = lambda s: str(s).replace("|", "\\|")
    linhas = ["| " + " | ".join(esc(c) for c in tab.colunas) + " |", "|" + "|".join("---" for _ in tab.colunas) + "|"]
    linhas += ["| " + " | ".join(esc(c) for c in l) + " |" for l in tab.linhas[:PREVIA_LINHAS]]
    return "\n".join(linhas)


def _gravar_extraido(d: Path, entrada: dict, ext: extracao.Extraido | None, cf: cofre.Cofre) -> tuple[str, str]:
    """Grava DOC-###.md e DOC-###.tabelas/*.csv pseudonimizados. Devolve (texto_md, texto_bruto_extraido)."""
    doc_id = entrada["doc_id"]
    cab = [f"# {doc_id} — {entrada['nome']}", _linha_tipo(entrada), ""]
    corpo: list[str] = []
    texto_claro: list[str] = []
    if ext is None:
        corpo.append("_(sem extração: formato não suportado)_")
    if ext and ext.paginas is not None:
        for n, pagina in enumerate(ext.paginas, start=1):
            anon, _ = cf.anonimizar(pagina)
            corpo += [f"<!-- p{n} -->", anon, ""]
            texto_claro.append(pagina)
    if ext and ext.linhas is not None:
        anon, _ = cf.anonimizar("\n".join(ext.linhas))
        corpo += ["<!-- l1 -->", anon, ""]
        texto_claro.append("\n".join(ext.linhas))
    if ext and ext.tabelas:
        pasta = d / "02_extraido" / f"{doc_id}.tabelas"
        pasta.mkdir(exist_ok=True)
        corpo.append("## Tabelas")
        for tab in ext.tabelas:
            tab_anon = _anonimizar_tabela(tab, cf)
            (pasta / f"{tab.nome}.csv").write_text(_csv_texto(tab_anon), encoding="utf-8")
            corpo += ["", f"### {tab.nome}.csv — {len(tab.linhas)} linhas × {len(tab.colunas)} colunas", "", _previa_md(tab_anon)]
            texto_claro.append(";".join(tab.colunas))
            texto_claro.extend(";".join(l) for l in tab.linhas[:50])
        corpo.append("")
    md = "\n".join(cab + corpo)
    (d / "02_extraido" / f"{doc_id}.md").write_text(md, encoding="utf-8")
    return md, "\n".join(texto_claro)


def _linha_tipo(entrada: dict) -> str:
    ex = entrada["extracao"]
    dim = f"{ex['paginas']} páginas" if ex.get("paginas") is not None else f"{ex['linhas']} linhas" if ex.get("linhas") is not None else f"{len(ex.get('tabelas', []))} tabelas"
    return f"tipo: {entrada['tipo']} · sha256: {entrada['sha256'][:8]} · formato: {ex['formato']} · {dim}"


def _repassar_dicionario(d: Path, cf: cofre.Cofre) -> int:
    """Reaplica o dicionário de nomes aos extraídos já gravados (nomes aprendidos depois)."""
    n = 0
    for p in sorted((d / "02_extraido").rglob("*")):
        if p.is_file() and p.suffix in (".md", ".csv"):
            texto = p.read_text(encoding="utf-8")
            novo, k = cf.anonimizar_dicionario(texto)
            if k:
                p.write_text(novo, encoding="utf-8")
                n += k
    return n


# ---------- caso.db ----------

def _gravar_documento(con, e: dict) -> None:
    ex = e["extracao"]
    con.execute(
        "insert or replace into documentos (doc_id, nome, sha256, tamanho, tipo, confianca, recebido_em, ingerido_em, formato, paginas, linhas, avisos)"
        " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (e["doc_id"], e["nome"], e["sha256"], e["tamanho"], e["tipo"], e["confianca_classificacao"], e["recebido_em"],
         e["ingerido_em"], ex.get("formato"), ex.get("paginas"), ex.get("linhas"), json.dumps(e["avisos"], ensure_ascii=False)),
    )


def _sincronizar_entidades(con, cf: cofre.Cofre, doc_id: str) -> None:
    con.executemany(
        "insert or ignore into entidades (pseudonimo, tipo, primeiro_doc) values (?, ?, ?)",
        [(e["pseudonimo"], e["tipo"], doc_id) for e in cf.entidades()],
    )


# ---------- ingerir ----------

def ingerir(codinome: str, reclassificar: str | None = None, tipo: str | None = None) -> dict:
    d = caso.caminho(codinome)
    if reclassificar or tipo:
        if not (reclassificar and tipo):
            raise caso.ErroCaso("--reclassificar exige --tipo (e vice-versa)")
        return _reclassificar(d, codinome, reclassificar, tipo)

    manifesto = _ler_manifesto(d)
    por_nome = {e["nome"]: e for e in manifesto}
    cf = cofre.Cofre(d / "_cofre" / "identidades.db")
    con = db.conectar(d / "caso.db")
    proximo = len(manifesto) + 1
    novos, ignorados, avisos, alertas = [], [], [], []

    for arq in sorted(p for p in (d / "00_brutos").iterdir() if p.is_file() and not p.name.startswith(".")):
        sha = custodia.sha256_arquivo(arq)
        if arq.name in por_nome:
            if por_nome[arq.name]["sha256"] == sha:
                ignorados.append(arq.name)
            else:
                doc_id = por_nome[arq.name]["doc_id"]
                msg = f"{arq.name}: hash divergente do manifesto ({doc_id}); bruto alterado após a ingestão — não reingerido"
                avisos.append(msg)
                alertas.append(msg)
                custodia.registrar(d, doc_id, "fixacao", "divergência de hash detectada em nova verificação", sha,
                                   {"sha256_manifesto": por_nome[arq.name]["sha256"]})
            continue

        doc_id = f"DOC-{proximo:03d}"
        proximo += 1
        tamanho = arq.stat().st_size
        recebido_em = dt.datetime.fromtimestamp(arq.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
        custodia.registrar(d, doc_id, "recebimento", f"arquivo {arq.name} localizado em 00_brutos ({tamanho} bytes)", sha)
        custodia.registrar(d, doc_id, "fixacao", "SHA-256 do bruto calculado", sha)

        entrada = {
            "doc_id": doc_id, "nome": arq.name, "sha256": sha, "tamanho": tamanho,
            "recebido_em": recebido_em, "ingerido_em": caso.agora(),
            "tipo": "OUTRO", "confianca_classificacao": 0.0,
            "extracao": {"formato": None, "paginas": None, "linhas": None, "tabelas": []},
            "avisos": [],
        }
        try:
            ext = extracao.extrair(arq)
        except extracao.NaoSuportado as e:
            ext = None
            entrada["avisos"].append(str(e))
        except Exception as e:  # bruto corrompido: registra e segue
            ext = None
            entrada["avisos"].append(f"falha na extração: {e}")

        if ext is not None:
            entrada["extracao"] = {
                "formato": ext.formato,
                "paginas": len(ext.paginas) if ext.paginas is not None else None,
                "linhas": len(ext.linhas) if ext.linhas is not None else None,
                "tabelas": [f"{t.nome}.csv" for t in ext.tabelas],
            }
            entrada["avisos"].extend(ext.avisos)
            cabecalhos = [c for t in ext.tabelas for c in t.colunas]
            entrada["tipo"], entrada["confianca_classificacao"] = classificacao.classificar(arq.name, ext.texto(), cabecalhos)

        md, texto_claro = _gravar_extraido(d, entrada, ext, cf)
        if RE_INSTRUCAO.search(cofre.sem_acentos(texto_claro).lower()):
            alertas.append(f"{doc_id}: texto com aparência de instrução ao modelo em {arq.name}; tratado como dado")

        ex = entrada["extracao"]
        custodia.registrar(d, doc_id, "processamento",
                           f"extração ({ex['formato'] or 'nenhuma'}; páginas={ex['paginas']}, linhas={ex['linhas']}, tabelas={len(ex['tabelas'])}) e pseudonimização", sha)
        custodia.registrar(d, doc_id, "armazenamento", f"gravado 02_extraido/{doc_id}.md", sha,
                           {"sha256_extraido": custodia.sha256_texto(md)})
        manifesto.append(entrada)
        novos.append(entrada)
        _gravar_documento(con, entrada)
        _sincronizar_entidades(con, cf, doc_id)
        avisos.extend(f"{doc_id}: {a}" for a in entrada["avisos"])

    if novos:
        _repassar_dicionario(d, cf)
    con.commit()
    con.close()
    _gravar_manifesto(d, manifesto)

    estado = caso.estado(codinome)
    sets = ["fase=ingestao"] if estado["fase"] == "novo" and manifesto else []
    caso.estado(codinome, sets=sets, adds=[f"alertas={a}" for a in alertas])

    return {
        "codinome": codinome,
        "documentos": len(manifesto),
        "por_tipo": dict(collections.Counter(e["tipo"] for e in manifesto)),
        "novos": novos,
        "ignorados": ignorados,
        "avisos": avisos,
        "alertas": alertas,
    }


def _reclassificar(d: Path, codinome: str, doc_id: str, tipo: str) -> dict:
    if tipo not in TIPOS:
        raise caso.ErroCaso(f"tipo desconhecido: {tipo!r}; use um de {list(TIPOS)}")
    manifesto = _ler_manifesto(d)
    entrada = next((e for e in manifesto if e["doc_id"] == doc_id), None)
    if entrada is None:
        raise caso.ErroCaso(f"{doc_id} não consta do manifesto de {codinome}")
    anterior = entrada["tipo"]
    entrada["tipo"], entrada["confianca_classificacao"] = tipo, 1.0
    _gravar_manifesto(d, manifesto)

    md = d / "02_extraido" / f"{doc_id}.md"
    if md.is_file():
        linhas = md.read_text(encoding="utf-8").split("\n")
        if len(linhas) > 1 and linhas[1].startswith("tipo: "):
            linhas[1] = _linha_tipo(entrada)
            md.write_text("\n".join(linhas), encoding="utf-8")
    con = db.conectar(d / "caso.db")
    con.execute("update documentos set tipo=?, confianca=1.0 where doc_id=?", (tipo, doc_id))
    con.commit()
    con.close()
    custodia.registrar(d, doc_id, "processamento", f"reclassificação manual: {anterior} → {tipo}", entrada["sha256"])
    return {"codinome": codinome, "reclassificado": {"doc_id": doc_id, "tipo": tipo}, "anterior": anterior}
