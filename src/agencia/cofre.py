"""Pseudonimização consistente por caso e cofre de identidades.

`_cofre/identidades.db` (600) guarda pseudônimo ↔ valor real. O modelo nunca o lê: só o
pacote `agencia` abre este arquivo. Tokens: PF-, PJ-, CT-, TEL-, EML-, PIX-, END-####.
"""
import collections
import datetime as dt
import os
import re
import unicodedata
from pathlib import Path

from agencia import caso, db

PREFIXOS = {
    "cpf": "PF", "nome_pf": "PF",
    "cnpj": "PJ", "nome_pj": "PJ",
    "conta": "CT", "telefone": "TEL", "email": "EML", "pix": "PIX", "endereco": "END",
}
TIPOS_NOME = ("nome_pf", "nome_pj", "endereco")
RE_TOKEN = re.compile(r"\b(PF|PJ|CT|TEL|EML|PIX|END)-\d{4}\b")

# ---------- validação de documentos ----------

def _digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _dv(digitos: str, pesos: list[int]) -> str:
    resto = sum(int(d) * p for d, p in zip(digitos, pesos)) % 11
    return "0" if resto < 2 else str(11 - resto)


def cpf_valido(d: str) -> bool:
    d = _digitos(d)
    if len(d) != 11 or d == d[0] * 11:
        return False
    d1 = _dv(d[:9], list(range(10, 1, -1)))
    d2 = _dv(d[:9] + d1, list(range(11, 1, -1)))
    return d[9:] == d1 + d2


def cnpj_valido(d: str) -> bool:
    d = _digitos(d)
    if len(d) != 14 or d == d[0] * 14:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _dv(d[:12], p1)
    d2 = _dv(d[:12] + d1, [6] + p1)
    return d[12:] == d1 + d2


def telefone_valido(s: str) -> bool:
    d = _digitos(s)
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) not in (10, 11) or not 11 <= int(d[:2]) <= 99:
        return False
    return d[2] == "9" if len(d) == 11 else d[2] in "2345"


# ---------- normalização ----------

def sem_acentos(texto: str) -> str:
    """Remove acentos preservando o comprimento (1 caractere de entrada -> 1 de saída)."""
    saida = []
    for ch in texto:
        dec = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in dec if not unicodedata.combining(c))
        saida.append(base[0] if base else ch)
    return "".join(saida)


def normalizar(tipo: str, valor: str) -> str:
    v = (valor or "").strip()
    if tipo in ("cpf", "cnpj"):
        return _digitos(v)
    if tipo == "telefone":
        d = _digitos(v)
        if len(d) in (12, 13) and d.startswith("55"):
            d = d[2:]
        return d
    if tipo == "email":
        return v.lower()
    if tipo == "conta":
        if "/" in v:
            ag, ct = v.split("/", 1)
            return f"{_digitos(ag)}/{_digitos(ct)}"
        return _digitos(v)
    if tipo in TIPOS_NOME:
        return re.sub(r"\s+", " ", sem_acentos(v).upper()).strip()
    return v


# ---------- padrões em texto livre ----------

RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RE_CNPJ = re.compile(r"(?<![\d\-/])\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?![\d\-/])")
RE_CPF = re.compile(r"(?<![\d\-/])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\d\-/])")
RE_TEL = re.compile(
    r"(?<![\w\-/.])(?:\+?55[\s\-.]?)?(?:\(\d{2}\)|\d{2})[\s\-.]?(?:9[\s.]?)?\d{4}[\s\-.]?\d{4}(?![\d\-/.])"
)
RE_CONTA = re.compile(
    r"(?i)\bag(?:[êe]ncia)?\.?[ \t]*(?:n[ºo°.]?[ \t]*)?:?[ \t]*(\d{3,5}(?:-?\d)?)[ \t]*[,;/\-]?[ \t]*"
    r"(?:c/?c|conta(?:[ \t]+corrente|[ \t]+poupan[çc]a)?|cc|c\.c\.)\.?[ \t]*(?:n[ºo°.]?[ \t]*)?:?[ \t]*(\d{3,12}(?:-?[\dxX])?)\b"
)
_PALAVRA = r"(?:(?!CPF\b|CNPJ\b)[A-ZÀ-Ú][A-Za-zÀ-ÿ'.]*)"
_LIGA = r"(?:da|de|do|das|dos|e|DA|DE|DO|DAS|DOS|E)"
RE_NOME_DOC = re.compile(
    rf"\b({_PALAVRA}(?:[ \t]+(?:{_PALAVRA}|{_LIGA})){{1,7}})"
    r"[ \t]*[,\-–]?[ \t]*(?:\([ \t]*(?:CPF|CNPJ)?[ \t]*:?[ \t]*|(?:CPF|CNPJ|CPF/CNPJ)[ \t]*:?[ \t]*(?:n[ºo°.]?)?[ \t]*)"
    r"((?:\d[.\-/]?){11,14})(?!\d)\)?"
)
RE_PJ_SUFIXO = re.compile(r"\b(LTDA|ME|EPP|EIRELI|S\.?A\.?|SS|MEI|CIA|LIMITADA)\b\.?$", re.I)


def tipo_nome(nome: str) -> str:
    return "nome_pj" if RE_PJ_SUFIXO.search(nome.strip()) else "nome_pf"


class Cofre:
    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        db.inicializar(self.caminho, db.DDL_COFRE)
        os.chmod(self.caminho, 0o600)
        self.con = db.conectar(self.caminho)

    # ---- registro ----

    def _proximo(self, prefixo: str) -> str:
        linha = self.con.execute("select ultimo from contadores where prefixo=?", (prefixo,)).fetchone()
        n = (linha["ultimo"] if linha else 0) + 1
        self.con.execute("insert or replace into contadores (prefixo, ultimo) values (?, ?)", (prefixo, n))
        return f"{prefixo}-{n:04d}"

    def buscar(self, tipo: str, valor: str) -> str | None:
        chave = f"{tipo}:{normalizar(tipo, valor)}"
        linha = self.con.execute("select pseudonimo from identidades where chave=?", (chave,)).fetchone()
        return linha["pseudonimo"] if linha else None

    def pseudonimo(self, tipo: str, valor: str) -> str:
        """Devolve o pseudônimo do valor, criando-o se for novo. Mesmo valor -> mesmo token."""
        if tipo not in PREFIXOS:
            raise ValueError(f"tipo desconhecido: {tipo}")
        existente = self.buscar(tipo, valor)
        if existente:
            return existente
        token = self._proximo(PREFIXOS[tipo])
        self._inserir(tipo, valor, token)
        return token

    def vincular(self, tipo: str, valor: str, pseudonimo: str) -> None:
        """Associa outro valor (ex.: nome) a um pseudônimo já existente."""
        if not self.buscar(tipo, valor):
            self._inserir(tipo, valor, pseudonimo)

    def _inserir(self, tipo: str, valor: str, token: str) -> None:
        self.con.execute(
            "insert or ignore into identidades (chave, tipo, pseudonimo, valor, criado_em) values (?, ?, ?, ?, ?)",
            (f"{tipo}:{normalizar(tipo, valor)}", tipo, token, valor.strip(),
             dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
        )
        self.con.commit()

    def entidades(self) -> list[dict]:
        linhas = self.con.execute("select distinct pseudonimo from identidades order by pseudonimo").fetchall()
        return [{"pseudonimo": l["pseudonimo"], "tipo": l["pseudonimo"].split("-")[0]} for l in linhas]

    # ---- anonimização ----

    def _dicionario(self) -> list[tuple[str, str, str]]:
        """[(valor_normalizado, tipo, pseudonimo)] dos nomes/endereços, maiores primeiro."""
        linhas = self.con.execute(
            "select valor, tipo, pseudonimo from identidades where tipo in (?, ?, ?)", TIPOS_NOME
        ).fetchall()
        itens = [(normalizar(l["tipo"], l["valor"]), l["tipo"], l["pseudonimo"]) for l in linhas]
        itens = [i for i in itens if len(i[0]) >= 5]
        return sorted(set(itens), key=lambda i: -len(i[0]))

    def _ocorrencias_dicionario(self, texto: str) -> list[tuple[int, int, str]]:
        """Spans (inicio, fim, token) de nomes conhecidos no texto, sem sobreposição."""
        plano = sem_acentos(texto).upper()
        spans: list[tuple[int, int, str]] = []
        ocupado = [False] * len(plano)
        for valor, _tipo, token in self._dicionario():
            padrao = r"\b" + r"\s+".join(re.escape(p) for p in valor.split()) + r"\b"
            for m in re.finditer(padrao, plano):
                if not any(ocupado[m.start():m.end()]):
                    spans.append((m.start(), m.end(), token))
                    for i in range(m.start(), m.end()):
                        ocupado[i] = True
        return sorted(spans)

    def anonimizar_dicionario(self, texto: str) -> tuple[str, int]:
        spans = self._ocorrencias_dicionario(texto)
        for ini, fim, token in reversed(spans):
            texto = texto[:ini] + token + texto[fim:]
        return texto, len(spans)

    def anonimizar(self, texto: str) -> tuple[str, collections.Counter]:
        """Substitui identificadores por tokens. Devolve (texto, contagens por tipo)."""
        cont: collections.Counter = collections.Counter()

        def nome_doc(m: re.Match) -> str:
            nome, doc = m.group(1), m.group(2)
            d = _digitos(doc)
            if len(d) == 11 and cpf_valido(d):
                tipo_doc, tipo_n, rot = "cpf", "nome_pf", "CPF"
            elif len(d) == 14 and cnpj_valido(d):
                tipo_doc, tipo_n, rot = "cnpj", "nome_pj", "CNPJ"
            else:
                return m.group(0)
            token = self.pseudonimo(tipo_doc, d)
            self.vincular(tipo_n, nome, token)
            cont[tipo_doc] += 1
            cont["nome"] += 1
            return f"{token} ({rot} {token})"

        def sub(tipo: str, valida=None):
            def f(m: re.Match) -> str:
                if valida and not valida(m.group(0)):
                    return m.group(0)
                cont[tipo] += 1
                return self.pseudonimo(tipo, m.group(0))
            return f

        def conta(m: re.Match) -> str:
            cont["conta"] += 1
            return self.pseudonimo("conta", f"{m.group(1)}/{m.group(2)}")

        texto = RE_NOME_DOC.sub(nome_doc, texto)
        texto = RE_EMAIL.sub(sub("email"), texto)
        texto = RE_CNPJ.sub(sub("cnpj", cnpj_valido), texto)
        texto = RE_CPF.sub(sub("cpf", cpf_valido), texto)
        texto = RE_CONTA.sub(conta, texto)
        texto = RE_TEL.sub(sub("telefone", telefone_valido), texto)
        texto, n = self.anonimizar_dicionario(texto)
        cont["dicionario"] += n
        return texto, cont

    # ---- reidentificação (só no render, fora do modelo) ----

    def representacoes(self) -> dict[str, str]:
        por_token: dict[str, dict[str, str]] = collections.defaultdict(dict)
        for l in self.con.execute("select pseudonimo, tipo, valor from identidades order by criado_em, chave"):
            por_token[l["pseudonimo"]].setdefault(l["tipo"], l["valor"])
        saida = {}
        for token, vals in por_token.items():
            nome = vals.get("nome_pf") or vals.get("nome_pj")
            if "cpf" in vals:
                ident, rot = vals["cpf"], "CPF"
            elif "cnpj" in vals:
                ident, rot = vals["cnpj"], "CNPJ"
            else:
                ident, rot = None, None
            if nome and ident:
                saida[token] = f"{nome} ({rot} {ident})"
            else:
                saida[token] = nome or ident or next(iter(vals.values()))
        return saida

    def reidentificar_contando(self, texto: str) -> tuple[str, int, int]:
        rep = self.representacoes()
        subst = restantes = 0

        def f(m: re.Match) -> str:
            nonlocal subst, restantes
            if m.group(0) in rep:
                subst += 1
                return rep[m.group(0)]
            restantes += 1
            return m.group(0)

        return RE_TOKEN.sub(f, texto), subst, restantes

    def reidentificar(self, texto: str) -> str:
        return self.reidentificar_contando(texto)[0]

    # ---- varredura ----

    def contar_vazamentos(self, texto: str) -> int:
        n = sum(1 for m in RE_CNPJ.finditer(texto) if cnpj_valido(m.group(0)))
        n += sum(1 for m in RE_CPF.finditer(texto) if cpf_valido(m.group(0)))
        n += sum(1 for _ in RE_EMAIL.finditer(texto))
        n += sum(1 for m in RE_TEL.finditer(texto) if telefone_valido(m.group(0)))
        n += len(self._ocorrencias_dicionario(texto))
        return n


def abrir(codinome: str) -> Cofre:
    return Cofre(caso.caminho(codinome) / "_cofre" / "identidades.db")


def vazamento(codinome: str, arquivo: str | None = None) -> dict:
    """Conta identificadores em claro nos extraídos (ou num arquivo do caso). Só contagens."""
    d = caso.caminho(codinome)
    cf = abrir(codinome)
    if arquivo:
        alvo = (d / arquivo).resolve()
        if d.resolve() not in alvo.parents or not alvo.is_file():
            raise caso.ErroCaso(f"arquivo {arquivo!r} não encontrado dentro do caso")
        alvos = [(arquivo, alvo)]
    else:
        base = d / "02_extraido"
        alvos = [(str(p.relative_to(base)), p) for p in sorted(base.rglob("*")) if p.is_file()]
    por_arquivo = {}
    for rel, p in alvos:
        try:
            texto = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        n = cf.contar_vazamentos(texto)
        if n:
            por_arquivo[rel] = n
    return {"total": sum(por_arquivo.values()), "por_arquivo": por_arquivo}
