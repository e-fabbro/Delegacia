"""Resultados agregados salvos (`--salvar`): viram fonte citável `[F:DOC-###:agg#nome]`.

Arquivo em 03_analises/_agg/<nome>.json + registro em caso.db (agregados) com hash, para que
`achados verificar` (F4) resolva o ponteiro e confira o número citado.
"""
import json
from pathlib import Path

from agencia import caso, custodia, db, util


def salvar(codinome: str, comando: str, parametros: dict, resultado: dict) -> dict:
    d = caso.caminho(codinome)
    partes = [comando] + [f"{k}{v}" for k, v in sorted(parametros.items()) if v not in (None, False, "")]
    nome = util.slug("_".join(str(p) for p in partes))
    pasta = d / "03_analises" / "_agg"
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / f"{nome}.json"
    docs = sorted(set(resultado.get("docs") or []))
    conteudo = json.dumps({"nome": nome, "comando": comando, "parametros": parametros, "docs": docs, "resultado": resultado},
                          ensure_ascii=False, indent=2, default=str)
    arq.write_text(conteudo, encoding="utf-8")
    con = db.abrir_caso(d)
    con.execute(
        "insert or replace into agregados (nome, comando, parametros, docs, arquivo, sha256, criado_em) values (?, ?, ?, ?, ?, ?, ?)",
        (nome, comando, json.dumps(parametros, ensure_ascii=False, default=str), json.dumps(docs), str(arq.relative_to(d)),
         custodia.sha256_texto(conteudo), caso.agora()),
    )
    con.commit()
    con.close()
    return {"agg": nome, "arquivo": str(arq.relative_to(d)), "ponteiros": [util.ponteiro(doc, f"agg#{nome}") for doc in docs]}


def carregar(codinome: str, nome: str) -> dict | None:
    d = caso.caminho(codinome)
    arq: Path = d / "03_analises" / "_agg" / f"{nome}.json"
    if not arq.is_file():
        return None
    return json.loads(arq.read_text(encoding="utf-8"))
