"""Saída da CLI: JSON por padrão, tabela Markdown com --md."""
import json
import sys


def _celula(valor) -> str:
    if isinstance(valor, (dict, list)):
        texto = json.dumps(valor, ensure_ascii=False)
    elif valor is None:
        texto = ""
    else:
        texto = str(valor)
    return texto.replace("|", "\\|").replace("\n", " ")


def tabela(linhas: list[dict], colunas: list[str] | None = None) -> str:
    if not linhas:
        return "_(vazio)_"
    colunas = colunas or list(dict.fromkeys(k for linha in linhas for k in linha))
    cab = "| " + " | ".join(colunas) + " |"
    sep = "|" + "|".join("---" for _ in colunas) + "|"
    corpo = ["| " + " | ".join(_celula(linha.get(c)) for c in colunas) + " |" for linha in linhas]
    return "\n".join([cab, sep, *corpo])


def markdown(obj) -> str:
    """dict -> tabela campo/valor (listas de dicts viram tabelas próprias); list -> tabela."""
    if isinstance(obj, list):
        return tabela(obj)
    simples = {k: v for k, v in obj.items() if not (isinstance(v, list) and v and all(isinstance(i, dict) for i in v))}
    blocos = [tabela([{"campo": k, "valor": v} for k, v in simples.items()])]
    for k, v in obj.items():
        if k not in simples:
            blocos.append(f"\n**{k}**\n\n{tabela(v)}")
    return "\n".join(blocos)


def emitir(obj, md: bool = False) -> None:
    if md:
        print(markdown(obj))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def erro(msg: str, codigo: int = 1) -> int:
    print(json.dumps({"erro": msg}, ensure_ascii=False), file=sys.stderr)
    return codigo
