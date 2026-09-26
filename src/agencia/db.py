"""SQLite por caso. `caso.db` guarda dados pseudonimizados; `_cofre/identidades.db` guarda
a tabela pseudônimo ↔ identidade real e só é aberto pelo Python.

Cada fase acrescenta o seu DDL em `DDL_CASO` / `DDL_COFRE`; `inicializar` é idempotente.
"""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

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

# F2
DDL_RIF = """
create table if not exists rif_cabecalho (
    doc_id          text primary key,
    numero          text,
    data            text,
    destinatario    text,
    origem          text,
    tipo_origem     text,
    pedido          text,
    periodo_inicio  text,
    periodo_fim     text,
    total_informado integer,
    paginas         integer,
    parseado_em     text not null
);
create table if not exists comunicacoes_rif (
    doc_id           text not null,
    num              integer not null,
    pagina           integer,
    tipo             text,
    comunicante      text,
    segmento         text,
    data_comunicacao text,
    periodo_inicio   text,
    periodo_fim      text,
    valor_centavos   integer,
    titular          text,
    envolvidos       text not null default '[]',
    enquadramento    text,
    informacoes      text,
    texto            text,
    primary key (doc_id, num)
);
"""

# F3
DDL_BANCO = """
create table if not exists contas (
    conta       text primary key,
    banco       text,
    titular     text,
    doc_extrato text,
    origem      text not null
);
create table if not exists relacionamentos_ccs (
    doc_id text not null,
    linha  integer not null,
    pessoa text,
    conta  text,
    banco  text,
    tipo   text,
    inicio text,
    fim    text,
    primary key (doc_id, linha)
);
create table if not exists transacoes (
    doc_id            text not null,
    tx_id             integer not null,
    conta             text not null,
    banco             text,
    data              text not null,
    historico         text,
    documento         text,
    valor_centavos    integer not null,
    natureza          text not null,
    saldo_centavos    integer,
    contraparte       text,
    contraparte_conta text,
    contraparte_banco text,
    local             text,
    tabela            text,
    linha             integer,
    primary key (doc_id, tx_id)
);
create index if not exists idx_transacoes_conta_data on transacoes (conta, data);
create index if not exists idx_transacoes_contraparte on transacoes (contraparte);
create table if not exists agregados (
    nome       text primary key,
    comando    text not null,
    parametros text not null,
    docs       text not null,
    arquivo    text not null,
    sha256     text not null,
    criado_em  text not null
);
"""

# F4
DDL_INTEGRACAO = """
create table if not exists vinculos (
    origem         text not null,
    destino        text not null,
    tipo           text not null,
    n              integer not null,
    total_centavos integer not null default 0,
    primeira       text,
    ultima         text,
    fontes         text not null default '[]',
    papeis         text not null default '[]',
    primary key (origem, destino, tipo)
);
"""

DDL_CASO: list[str] = [DDL_META, DDL_DOCUMENTOS, DDL_RIF, DDL_BANCO, DDL_INTEGRACAO]
DDL_COFRE: list[str] = [DDL_META, DDL_IDENTIDADES]


def conectar(caminho: Path) -> sqlite3.Connection:
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    return con


def abrir_caso(caso_dir: Path) -> sqlite3.Connection:
    """Abre caso.db garantindo o DDL da versão atual (migração idempotente de casos antigos)."""
    caminho = caso_dir / "caso.db"
    inicializar(caminho, DDL_CASO)
    return conectar(caminho)


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
