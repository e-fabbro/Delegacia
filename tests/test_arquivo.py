"""`caso arquivar` / `caso desarquivar`: pacote cifrado, verificação, custódia e descarte."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from agencia import arquivo, caso, ingestao
from tests.fixtures.gerar import PF1

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SENHA = "senha-de-teste-bem-longa-2026"


def cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "agencia", *args], capture_output=True, text=True, env={**os.environ, **(env or {})})


@pytest.fixture
def caso_pronto(raiz_casos, monkeypatch):
    caso.novo("TESTE")
    d = raiz_casos / "TESTE"
    for nome in ("rif_sintetico.pdf", "simba_sintetico.csv"):
        shutil.copy(FIXTURES / nome, d / "00_brutos" / nome)
    ingestao.ingerir("TESTE")
    (d / "04_produtos" / "informacao_analise_v1.md").write_text("# Produto\nPF-0001 [F:DOC-001:p1].\n", encoding="utf-8")
    caso.estado("TESTE", sets=["fase=concluido"])
    monkeypatch.setenv(arquivo.VAR_SENHA, SENHA)
    return d


def test_cifra_e_decifra_stream_com_varios_blocos(monkeypatch):
    monkeypatch.setattr(arquivo, "BLOCO", 1000)
    claro = os.urandom(3505)                     # 3 blocos cheios + 1 parcial
    ent, cif = io.BytesIO(claro), io.BytesIO()
    sha, n_claro, n_cif = arquivo.cifrar_stream(ent, cif, b"segredo-longo-12")
    assert sha == hashlib.sha256(claro).hexdigest() and n_claro == 3505 and n_cif == cif.tell()
    assert claro[:200] not in cif.getvalue()
    cif.seek(0)
    dec = io.BytesIO()
    assert arquivo.decifrar_stream(cif, dec, b"segredo-longo-12") == sha and dec.getvalue() == claro
    # senha errada
    cif.seek(0)
    with pytest.raises(caso.ErroCaso, match="senha incorreta"):
        arquivo.decifrar_stream(cif, io.BytesIO(), b"outra-senha-longa")
    # adulteração de um byte do último bloco
    dados = bytearray(cif.getvalue()); dados[-3] ^= 0x01
    with pytest.raises(caso.ErroCaso):
        arquivo.decifrar_stream(io.BytesIO(bytes(dados)), io.BytesIO(), b"segredo-longo-12")
    # truncamento (remove o último bloco) não passa despercebido
    cif.seek(0)
    cab = cif.read(33)
    blocos = []
    while True:
        t = cif.read(4)
        if not t:
            break
        blocos.append(t + cif.read(int.from_bytes(t, "big")))
    truncado = io.BytesIO(cab + b"".join(blocos[:-1]))
    with pytest.raises(caso.ErroCaso):
        arquivo.decifrar_stream(truncado, io.BytesIO(), b"segredo-longo-12")
    # tamanho exato de múltiplo do bloco também termina com marcador de fim
    claro2 = os.urandom(2000)
    cif2 = io.BytesIO()
    arquivo.cifrar_stream(io.BytesIO(claro2), cif2, b"segredo-longo-12")
    cif2.seek(0)
    dec2 = io.BytesIO()
    arquivo.decifrar_stream(cif2, dec2, b"segredo-longo-12")
    assert dec2.getvalue() == claro2


def test_arquivar_cifrado_verifica_e_mantem_diretorio(caso_pronto, raiz_casos):
    r = arquivo.arquivar("TESTE")
    pacote, side = Path(r["pacote"]), Path(r["sidecar"])
    assert pacote.parent == raiz_casos / "_arquivo" and pacote.name.endswith(".tar.gz.enc") and side.is_file()
    assert r["cifrado"] and r["verificado"] and r["documentos"] == 2 and r["diretorio_apagado"] is False and "mantido" in r["aviso"]
    assert oct(pacote.stat().st_mode & 0o777) == "0o600"
    assert hashlib.sha256(pacote.read_bytes()).hexdigest() == r["sha256_pacote"]
    dados = pacote.read_bytes()
    assert dados.startswith(arquivo.MAGICO) and b"PF-0001" not in dados and PF1["nome"].encode() not in dados and b"identidades.db" not in dados
    sc = json.loads(side.read_text(encoding="utf-8"))
    assert sc["cifrado"] and sc["verificado"] and sc["arquivos_no_pacote"] == r["arquivos_no_pacote"] >= 10
    assert [d["doc_id"] for d in sc["documentos"]] == ["DOC-001", "DOC-002"] and sc["custodia_descarte"] == []
    assert sc["cifra"]["algoritmo"].startswith("AES-256-GCM") and "scrypt" in sc["cifra"]["kdf"]
    # estado e custódia dentro do caso (que ficou) refletem o arquivamento
    est = caso.estado("TESTE")
    assert est["fase"] == "arquivado" and any("arquivado em" in a for a in est["alertas"])
    cadeia = [json.loads(l) for l in (caso_pronto / "01_custodia" / "cadeia.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for e in cadeia if e["etapa"] == "acondicionamento") == 2


def test_arquivar_e_apagar_depois_restaurar(caso_pronto, raiz_casos, tmp_path):
    cadeia_antes = (caso_pronto / "01_custodia" / "cadeia.jsonl").read_text(encoding="utf-8")
    r = arquivo.arquivar("TESTE", apagar=True)
    assert r["diretorio_apagado"] and not caso_pronto.exists()
    sc = json.loads(Path(r["sidecar"]).read_text(encoding="utf-8"))
    assert sc["diretorio_apagado"] and [e["etapa"] for e in sc["custodia_descarte"]] == ["descarte", "descarte"]
    with pytest.raises(caso.ErroCaso):
        caso.status("TESTE")
    # restauração íntegra
    d = arquivo.desarquivar(r["pacote"], str(tmp_path / "restaurado"))
    assert d["conferido_com_sidecar"] and d["sha256_tar_gz"] == sc["sha256_tar_gz"]
    rest = tmp_path / "restaurado" / "TESTE"
    assert (rest / "00_brutos" / "rif_sintetico.pdf").read_bytes() == (FIXTURES / "rif_sintetico.pdf").read_bytes()
    assert (rest / "_cofre" / "identidades.db").is_file() and (rest / "caso.db").is_file()
    assert (rest / "04_produtos" / "informacao_analise_v1.md").read_text(encoding="utf-8").startswith("# Produto")
    cadeia_dep = (rest / "01_custodia" / "cadeia.jsonl").read_text(encoding="utf-8")
    assert cadeia_dep.startswith(cadeia_antes) and "acondicionamento" in cadeia_dep
    assert json.loads((rest / "estado.json").read_text(encoding="utf-8"))["fase"] == "arquivado"


def test_recusas(caso_pronto, raiz_casos, monkeypatch):
    caso.estado("TESTE", sets=["fase=analise"])
    with pytest.raises(caso.ErroCaso, match="fase"):
        arquivo.arquivar("TESTE")
    caso.estado("TESTE", sets=["fase=concluido"])
    monkeypatch.delenv(arquivo.VAR_SENHA)
    with pytest.raises(caso.ErroCaso, match="sem senha"):
        arquivo.arquivar("TESTE")
    monkeypatch.setenv(arquivo.VAR_SENHA, "curta")
    with pytest.raises(caso.ErroCaso, match="12 caracteres"):
        arquivo.arquivar("TESTE")
    monkeypatch.setenv(arquivo.VAR_SENHA, SENHA)
    with pytest.raises(caso.ErroCaso, match="dentro do diretório"):
        arquivo.arquivar("TESTE", destino=str(caso_pronto / "04_produtos"))
    assert caso_pronto.exists()
    # senha errada na restauração não apaga nada nem restaura
    r = arquivo.arquivar("TESTE")
    monkeypatch.setenv(arquivo.VAR_SENHA, "senha-errada-mas-longa")
    with pytest.raises(caso.ErroCaso, match="senha incorreta"):
        arquivo.desarquivar(r["pacote"], str(raiz_casos / "rest"))
    assert not (raiz_casos / "rest" / "TESTE").exists()


def test_sem_cifrar_e_forcar(caso_pronto, raiz_casos, tmp_path, monkeypatch):
    monkeypatch.delenv(arquivo.VAR_SENHA)
    caso.estado("TESTE", sets=["fase=analise"])
    r = arquivo.arquivar("TESTE", sem_cifrar=True, forcar=True, destino=str(tmp_path / "arq"))
    assert not r["cifrado"] and r["verificado"] and r["pacote"].endswith(".tar.gz")
    with tarfile.open(r["pacote"]) as tar:
        nomes = tar.getnames()
    assert "TESTE/estado.json" in nomes and any(n.endswith("identidades.db") for n in nomes)
    d = arquivo.desarquivar(r["pacote"], str(tmp_path / "rest"))
    assert d["arquivos"] == len(nomes) and (tmp_path / "rest" / "TESTE" / "caso.db").is_file()


def test_senha_em_arquivo_e_cli(caso_pronto, raiz_casos, tmp_path, monkeypatch):
    monkeypatch.delenv(arquivo.VAR_SENHA)
    senha_arq = tmp_path / "senha.txt"
    senha_arq.write_text(SENHA + "\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != arquivo.VAR_SENHA}
    r = subprocess.run([sys.executable, "-m", "agencia", "caso", "arquivar", "TESTE", "--senha-arquivo", str(senha_arq), "--apagar"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    saida = json.loads(r.stdout)
    assert saida["diretorio_apagado"] and not caso_pronto.exists()
    r = subprocess.run([sys.executable, "-m", "agencia", "caso", "desarquivar", saida["pacote"], "--destino", str(tmp_path / "r"), "--senha-arquivo", str(senha_arq)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "r" / "TESTE" / "estado.json").is_file()
    r = subprocess.run([sys.executable, "-m", "agencia", "caso", "desarquivar", saida["pacote"], "--destino", str(tmp_path / "r2")], capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "sem senha" in r.stderr
