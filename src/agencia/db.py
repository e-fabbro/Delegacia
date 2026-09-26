"""SQLite por caso. `caso.db` guarda dados pseudonimizados; `_cofre/identidades.db` guarda
a tabela pseudônimo ↔ identidade real e só é aberto pelo Python.

Cada fase acrescenta o seu DDL em `DDL_CASO` / `DDL_COFRE`; `inicializar` é idempotente.
"""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

DDL_META = """
create table if not exists meta (
    chave text primary key,
    valor text not null
);
"""

# F1
DDL_DOCUMENTOS = """
create table if not exists documentos (
    doc_id      text primary key,
    nome        text not null,
    sha256      text not null,
    tamanho     integer not null,
    tipo        text not null,
    confianca   real not null,
    recebido_em text not null,
    ingerido_em text not null,
    formato     text,
    paginas     integer,
    linhas      integer,
    avisos      text not null default '[]'
);
create table if not exists entidades (
    pseudonimo  text primary key,
    tipo        text not null,
    primeiro_doc text
);
"""

DDL_IDENTIDADES = """
create table if not exists identidades (
    chave      text primary key,
    tipo       text not null,
    pseudonimo text not null,
    valor      text not null,
    criado_em  text not null
);
create index if not exists idx_identidades_pseudonimo on identidades (pseudonimo);
create table if not exists contadores (
    prefixo text primary key,
    ultimo  integer not null
);
"""

DDL_CASO: list[str] = [DDL_META, DDL_DOCUMENTOS]
DDL_COFRE: list[str] = [DDL_META, DDL_IDENTIDADES]


def conectar(caminho: Path) -> sqlite3.Connection:
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    return con


def inicializar(caminho: Path, ddl: list[str]) -> None:
    con = conectar(caminho)
    try:
        for bloco in ddl:
            con.executescript(bloco)
        con.execute(
            "insert or replace into meta (chave, valor) values ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        con.commit()
    finally:
        con.close()
