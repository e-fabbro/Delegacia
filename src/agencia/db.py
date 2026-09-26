"""SQLite por caso. `caso.db` guarda dados pseudonimizados; `_cofre/identidades.db` guarda
a tabela pseudônimo ↔ identidade real e só é aberto pelo Python.

Cada fase acrescenta o seu DDL em `DDL_CASO` / `DDL_COFRE`; `inicializar` é idempotente.
"""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 0

DDL_META = """
create table if not exists meta (
    chave text primary key,
    valor text not null
);
"""

DDL_CASO: list[str] = [DDL_META]
DDL_COFRE: list[str] = [DDL_META]


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
