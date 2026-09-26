"""`caso arquivar` / `caso desarquivar`: descarte do caso ao fim do procedimento.

arquivar: fase → arquivado, custódia (acondicionamento) por documento, tar.gz de TODO o diretório do caso
(inclusive 00_brutos/ e _cofre/, que só o Python lê), cifrado com AES-256-GCM em blocos (chave derivada
da senha por scrypt), sidecar .arquivo.json com hashes e parâmetros, verificação por decifragem e, só
então, com --apagar, remoção do diretório de trabalho (custódia: descarte, registrada no sidecar).

A senha nunca vai na linha de comando: variável AGENCIA_ARQUIVO_SENHA ou arquivo indicado em --senha-arquivo.
Sem senha só se arquiva com --sem-cifrar (explícito). O pacote em claro nunca fica em disco quando cifrado.
"""
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import struct
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from agencia import caso, custodia

MAGICO = b"AGNX1"                # formato do contêiner
BLOCO = 1 << 20                  # 1 MiB de texto claro por bloco
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "length": 32}
VAR_SENHA = "AGENCIA_ARQUIVO_SENHA"
SUFIXO_CIFRADO = ".tar.gz.enc"
SUFIXO_CLARO = ".tar.gz"


# ---------- senha e chave ----------

def _senha(senha_arquivo: str | None) -> bytes | None:
    if senha_arquivo:
        p = Path(senha_arquivo)
        if not p.is_file():
            raise caso.ErroCaso(f"arquivo de senha não encontrado: {senha_arquivo}")
        s = p.read_text(encoding="utf-8").strip()
    else:
        s = (os.environ.get(VAR_SENHA) or "").strip()
    if not s:
        return None
    if len(s) < 12:
        raise caso.ErroCaso("senha do arquivo com menos de 12 caracteres")
    return s.encode("utf-8")


def _chave(senha: bytes, sal: bytes) -> bytes:
    return Scrypt(salt=sal, **SCRYPT).derive(senha)


# ---------- contêiner cifrado (cabeçalho + blocos AES-GCM) ----------

def cifrar_stream(entrada, saida, senha: bytes) -> tuple[str, int, int]:
    """Cifra `entrada` (arquivo binário) em `saida`. Devolve (sha256 do claro, bytes claros, bytes cifrados)."""
    sal, base_nonce = os.urandom(16), os.urandom(8)
    chave = _chave(senha, sal)
    aes = AESGCM(chave)
    cab = MAGICO + struct.pack(">I", BLOCO) + sal + base_nonce
    saida.write(cab)
    h = hashlib.sha256()
    claros, cifrados, i = 0, len(cab), 0
    while True:
        bloco = entrada.read(BLOCO)
        ultimo = len(bloco) < BLOCO
        h.update(bloco)
        claros += len(bloco)
        nonce = base_nonce + struct.pack(">I", i)
        # o último bloco leva marcador no dado associado: impede truncamento silencioso
        ct = aes.encrypt(nonce, bloco, b"fim" if ultimo else b"seg")
        saida.write(struct.pack(">I", len(ct)) + ct)
        cifrados += 4 + len(ct)
        i += 1
        if ultimo:
            break
    return h.hexdigest(), claros, cifrados


def decifrar_stream(entrada, saida, senha: bytes) -> str:
    """Decifra o contêiner em `saida` (None = só verifica). Devolve o sha256 do texto claro. Erro em senha errada ou adulteração."""
    cab = entrada.read(len(MAGICO) + 4 + 16 + 8)
    if not cab.startswith(MAGICO):
        raise caso.ErroCaso("arquivo não é um pacote da agência (cabeçalho inválido)")
    bloco_tam = struct.unpack(">I", cab[5:9])[0]
    sal, base_nonce = cab[9:25], cab[25:33]
    aes = AESGCM(_chave(senha, sal))
    h = hashlib.sha256()
    i = 0
    while True:
        tam = entrada.read(4)
        if len(tam) != 4:
            raise caso.ErroCaso("pacote truncado (bloco sem tamanho)")
        ct = entrada.read(struct.unpack(">I", tam)[0])
        nonce = base_nonce + struct.pack(">I", i)
        try:
            claro = aes.decrypt(nonce, ct, b"seg")
            ultimo = False
        except Exception:
            try:
                claro = aes.decrypt(nonce, ct, b"fim")
                ultimo = True
            except Exception:
                raise caso.ErroCaso(f"falha ao decifrar o bloco {i}: senha incorreta ou pacote adulterado") from None
        h.update(claro)
        if saida is not None:
            saida.write(claro)
        i += 1
        if ultimo:
            break
        if len(claro) != bloco_tam:
            raise caso.ErroCaso("pacote inconsistente (bloco intermediário curto)")
    if entrada.read(1):
        raise caso.ErroCaso("pacote com dados após o bloco final")
    return h.hexdigest()


# ---------- arquivar ----------

def _tar_gz(caso_dir: Path, destino_tmp: Path) -> tuple[str, int, int]:
    """tar.gz determinístico o bastante (ordem fixa) do diretório do caso. Devolve (sha256, bytes, arquivos)."""
    n = 0
    with open(destino_tmp, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for p in sorted(caso_dir.rglob("*")):
            tar.add(p, arcname=str(Path(caso_dir.name) / p.relative_to(caso_dir)), recursive=False)
            if p.is_file():
                n += 1
    return custodia.sha256_arquivo(destino_tmp), destino_tmp.stat().st_size, n


def arquivar(codinome: str, destino: str | None = None, apagar: bool = False, sem_cifrar: bool = False,
             senha_arquivo: str | None = None, forcar: bool = False) -> dict:
    d = caso.caminho(codinome)
    raiz = caso.raiz_casos()
    estado = caso.estado(codinome)
    if estado["fase"] not in ("concluido", "arquivado") and not forcar:
        raise caso.ErroCaso(f"caso em fase {estado['fase']!r}; arquive só em 'concluido' (ou use --forcar)")
    senha = None if sem_cifrar else _senha(senha_arquivo)
    if not sem_cifrar and senha is None:
        raise caso.ErroCaso(f"sem senha: defina {VAR_SENHA}, use --senha-arquivo <arquivo> ou peça --sem-cifrar explicitamente")
    pasta = Path(destino) if destino else raiz / "_arquivo"
    pasta.mkdir(parents=True, exist_ok=True)
    if d.resolve() == pasta.resolve() or d.resolve() in pasta.resolve().parents:
        raise caso.ErroCaso("o destino não pode ficar dentro do diretório do caso")

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = pasta / f"{codinome}_{ts}"
    manifesto = json.loads((d / "01_custodia" / "manifesto.json").read_text(encoding="utf-8")) if (d / "01_custodia" / "manifesto.json").is_file() else []

    # 1) estado e custódia (acondicionamento) ficam DENTRO do pacote
    caso.estado(codinome, sets=["fase=arquivado"], adds=[f"alertas=arquivado em {ts} → {base.name}"])
    for e in manifesto:
        custodia.registrar(d, e["doc_id"], "acondicionamento", f"incluído no pacote de arquivamento {base.name}", e["sha256"])

    # 2) tar.gz em arquivo temporário (0600, na pasta de destino)
    fd, tmp = tempfile.mkstemp(prefix=f".{codinome}_", suffix=".tar.gz", dir=pasta)
    os.close(fd)
    tmp = Path(tmp)
    os.chmod(tmp, 0o600)
    try:
        sha_claro, tam_claro, n_arquivos = _tar_gz(d, tmp)
        if senha:
            pacote = base.with_name(base.name + SUFIXO_CIFRADO)
            with open(tmp, "rb") as ent, open(pacote, "wb") as sai:
                os.chmod(pacote, 0o600)
                sha_c, _, tam_cifrado = cifrar_stream(ent, sai, senha)
            assert sha_c == sha_claro
            # 3) verificação: decifra e confere o hash
            with open(pacote, "rb") as ent:
                sha_verif = decifrar_stream(ent, None, senha)
            verificado = sha_verif == sha_claro
        else:
            pacote = base.with_name(base.name + SUFIXO_CLARO)
            shutil.move(tmp, pacote)
            os.chmod(pacote, 0o600)
            tam_cifrado = None
            verificado = custodia.sha256_arquivo(pacote) == sha_claro
    finally:
        if tmp.exists():
            tmp.unlink()

    sidecar = {
        "codinome": codinome, "arquivado_em": ts, "pacote": pacote.name, "cifrado": bool(senha),
        "cifra": {"algoritmo": "AES-256-GCM por bloco", "bloco_bytes": BLOCO, "kdf": {"scrypt": SCRYPT}} if senha else None,
        "sha256_tar_gz": sha_claro, "tamanho_tar_gz": tam_claro,
        "sha256_pacote": custodia.sha256_arquivo(pacote), "tamanho_pacote": pacote.stat().st_size,
        "arquivos_no_pacote": n_arquivos, "verificado": verificado,
        "documentos": [{"doc_id": e["doc_id"], "nome": e["nome"], "sha256": e["sha256"], "tipo": e["tipo"]} for e in manifesto],
        "diretorio_apagado": False, "custodia_descarte": [],
        "restaurar": f"python -m agencia caso desarquivar {pacote} --destino <DIR>" + ("" if senha else " --sem-cifrar"),
    }
    if not verificado:
        pacote.unlink(missing_ok=True)
        raise caso.ErroCaso("verificação do pacote falhou; nada foi apagado e o pacote foi removido")

    if apagar:
        for e in manifesto:
            sidecar["custodia_descarte"].append({
                "ts": caso.agora(), "doc_id": e["doc_id"], "etapa": "descarte", "sha256": e["sha256"],
                "descricao": f"diretório de trabalho removido após verificação do pacote {pacote.name}",
            })
        shutil.rmtree(d)
        sidecar["diretorio_apagado"] = True

    side = base.with_name(base.name + ".arquivo.json")
    side.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(side, 0o600)
    return {"codinome": codinome, "pacote": str(pacote), "sidecar": str(side), "cifrado": bool(senha), "verificado": verificado,
            "sha256_pacote": sidecar["sha256_pacote"], "tamanho_pacote": sidecar["tamanho_pacote"], "documentos": len(manifesto),
            "arquivos_no_pacote": n_arquivos, "diretorio_apagado": sidecar["diretorio_apagado"],
            "aviso": None if apagar else "diretório de trabalho mantido; repita com --apagar para descartar (após conferir o pacote)"}


# ---------- desarquivar ----------

def desarquivar(pacote: str, destino: str, sem_cifrar: bool = False, senha_arquivo: str | None = None) -> dict:
    p = Path(pacote)
    if not p.is_file():
        raise caso.ErroCaso(f"pacote não encontrado: {pacote}")
    dest = Path(destino)
    dest.mkdir(parents=True, exist_ok=True)
    side = p.with_name(p.name.replace(SUFIXO_CIFRADO, "").replace(SUFIXO_CLARO, "") + ".arquivo.json")
    esperado = json.loads(side.read_text(encoding="utf-8")).get("sha256_tar_gz") if side.is_file() else None
    fd, tmp = tempfile.mkstemp(prefix=".restaura_", suffix=".tar.gz", dir=dest)
    os.close(fd)
    tmp = Path(tmp)
    try:
        if sem_cifrar or p.name.endswith(SUFIXO_CLARO):
            shutil.copy(p, tmp)
            sha = custodia.sha256_arquivo(tmp)
        else:
            senha = _senha(senha_arquivo)
            if senha is None:
                raise caso.ErroCaso(f"sem senha: defina {VAR_SENHA} ou use --senha-arquivo")
            with open(p, "rb") as ent, open(tmp, "wb") as sai:
                sha = decifrar_stream(ent, sai, senha)
        if esperado and sha != esperado:
            raise caso.ErroCaso("hash do tar.gz restaurado difere do registrado no .arquivo.json")
        with tarfile.open(tmp, "r:gz") as tar:
            nomes = tar.getnames()
            raiz_pacote = nomes[0].split("/")[0] if nomes else None
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise caso.ErroCaso(f"caminho inseguro no pacote: {m.name}")
            tar.extractall(dest, filter="data")
    finally:
        tmp.unlink(missing_ok=True)
    return {"pacote": str(p), "destino": str(dest / raiz_pacote) if raiz_pacote else str(dest), "sha256_tar_gz": sha,
            "conferido_com_sidecar": bool(esperado), "arquivos": len(nomes)}
