# Operação da Agência Nexo na VPS

Tudo aqui é executado pelo Fabbro, como root, salvo indicação. O Nexo roda como o usuário Unix `nexo`,
no Codex, com login ChatGPT próprio. Nunca use chave de API.

## 1. Depois de um reboot

1. **Montar os casos** (pede a senha do gocryptfs, guardada fora da VPS):
   ```
   nexo-montar-casos
   sudo -u nexo mountpoint /srv/casos        # "is a mountpoint"
   ```
   Root não enxerga `/srv/casos` montado (sem `allow_other`). Isso é esperado.
2. **Firewall do nexo** (sobe sozinho):
   ```
   systemctl is-active nexo-egress-nft.service nexo-egress-refresh.timer
   nft list table inet nexo_egress | grep -c permitidos      # sets presentes
   sudo -u nexo -i bash -c 'curl -sS -m 5 https://example.com >/dev/null 2>&1 && echo LIVRE || echo BLOQUEADO'
   ```
   Tem de dar `BLOQUEADO`. As tabelas do Docker não são tocadas: nunca habilite `nftables.service`.
3. **Codex do nexo**:
   ```
   sudo -u nexo -i codex login status        # "Logged in using ChatGPT"
   sudo -u nexo -i bash -lc 'cd /opt/agencia-nexo && ops/teste_headless.sh TESTE'
   ```
   Se o login tiver caído: `sudo -u nexo -i codex login --device-auth` (login próprio; nunca copie `auth.json`
   de outro perfil).
4. **Ponte do Hermes**:
   ```
   systemctl is-active ponte-nexo
   /root/.hermes/profiles/nexo/scripts/agencia resultado TESTE     # não gasta cota
   ```

## 2. Onde ficam os logs

| O quê | Onde | Observação |
|---|---|---|
| Auditoria dos hooks (toda chamada de ferramenta, inclusive bloqueios) | `/var/log/agencia-nexo/auditoria.jsonl` | só comandos, truncados; rotação mensal, 5 anos |
| Eventos e stderr do Codex por execução | `/srv/casos/<COD>/log/codex_<acao>_<ts>.jsonl` e `.err` | dentro do volume cifrado; vão no pacote de arquivamento |
| Resultado de cada execução | `/srv/casos/<COD>/log/headless_*.json`, `run_*.json` (+ `.done`) | `result` é o resumo pseudonimizado |
| Descartes do firewall do nexo | `journalctl -k \| grep nexo-egress-drop` | destino e porta |
| Ponte | `journalctl -u ponte-nexo` | não registra pedidos (a auditoria fica na Agência) |

Os logs dos casos não passam por logrotate: root não enxerga o volume montado, e cada execução gera arquivo próprio
que acompanha o caso até o arquivamento.

## 3. Limite de uso (cota ChatGPT)

A conta do `nexo` é a mesma dos gateways do Hermes: a cota é compartilhada. Se uma execução parar com
`usage limit` (no `.jsonl` do caso, evento `turn.failed`), nada se perde: o `estado.json` registra o que foi
concluído e aprovado. Depois da hora indicada na mensagem (horário de Brasília), repita a mesma ação: o Nexo
retoma do ponto em que parou.

## 4. Reprocessar um caso

- Retomar ou refazer a análise: `nexo_run analisar <COD>` (ou `agencia analisar <COD>` pelo Hermes).
  Agentes já aprovados não são refeitos.
- Material novo: deposite em `/srv/casos/<COD>/00_brutos/` como `nexo`
  (`sudo -u nexo cp <arquivo> /srv/casos/<COD>/00_brutos/`) e rode `nexo_run ingerir <COD>`.
- Conferências determinísticas, sem gastar cota:
  ```
  sudo -u nexo bash -c 'cd /opt/agencia-nexo && .venv/bin/python -m agencia caso status <COD> --md'
  sudo -u nexo bash -c 'cd /opt/agencia-nexo && .venv/bin/python -m agencia cofre vazamento <COD>'
  ```

## 5. Arquivar (só por ordem do Fabbro)

A senha de arquivamento é do Fabbro, fica fora da VPS e nunca vai na linha de comando nem para o Nexo.

```
sudo -u nexo bash -c 'umask 077; cat > /home/nexo/.senha_arquivo'       # cole a senha, Enter, Ctrl-D
sudo -u nexo bash -c 'cd /opt/agencia-nexo && .venv/bin/python -m agencia caso arquivar <COD> --senha-arquivo /home/nexo/.senha_arquivo --md'
```
Confira o `sha256_pacote` devolvido. Só depois, e se quiser remover o diretório de trabalho:
```
sudo -u nexo bash -c 'cd /opt/agencia-nexo && .venv/bin/python -m agencia caso arquivar <COD> --senha-arquivo /home/nexo/.senha_arquivo --apagar --md'
sudo -u nexo shred -u /home/nexo/.senha_arquivo
```
Fora da fase `concluido` o arquivamento exige `--forcar`. `--sem-cifrar` só para caso sintético.
O pacote vai para `/srv/casos/_arquivo/<COD>_<ts>.tar.gz.enc`, com o sidecar `.arquivo.json` (hashes e custódia).

## 6. Restaurar

```
sudo -u nexo bash -c 'umask 077; cat > /home/nexo/.senha_arquivo'
sudo -u nexo bash -c 'cd /opt/agencia-nexo && .venv/bin/python -m agencia caso desarquivar /srv/casos/_arquivo/<pacote>.tar.gz.enc --destino /srv/casos --senha-arquivo /home/nexo/.senha_arquivo --md'
sudo -u nexo shred -u /home/nexo/.senha_arquivo
```
O comando confere o hash do pacote antes de restaurar.

## 7. Backup fora da VPS

- **O que copiar:** só os pacotes cifrados `/srv/casos/_arquivo/*.tar.gz.enc` e os sidecars `.arquivo.json`.
  Nunca copie `/srv/casos/<COD>/` em claro nem `/srv/.casos.cifrado/` sem a senha guardada à parte.
- **Como:** como `nexo`, para um destino fora da VPS escolhido pelo Fabbro (disco institucional ou armazenamento
  cifrado próprio). O egress do `nexo` é bloqueado; a cópia é feita por root a partir de um diretório de
  saída preparado pelo `nexo`:
  ```
  sudo -u nexo cp /srv/casos/_arquivo/<COD>_*.tar.gz.enc /srv/casos/_arquivo/<COD>_*.arquivo.json /home/nexo/saida/
  # root copia /home/nexo/saida/ para o destino e depois apaga os arquivos de lá
  ```
- **Senhas, sempre fora da VPS e separadas do backup:** a do gocryptfs (com a master key) e a de arquivamento,
  no gerenciador de senhas do Fabbro. Sem a senha de arquivamento, o pacote é irrecuperável.

## 8. Atualizar o código

```
git -C /opt/agencia-nexo pull --ff-only
cd /opt/agencia-nexo && bash ops/setup_vps.sh          # idempotente; não pede senha se o volume já existe
bash ops/instalar_ponte.sh && systemctl restart hermes-gateway-nexo    # se hermes/ mudou
```
O repositório fica `root:root`: o `nexo` só lê o código (o sandbox do Codex grava no diretório de trabalho).
