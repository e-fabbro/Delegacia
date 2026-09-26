[CONSTRUÇÃO] Prompt de finalização — Agência Nexo na VPS

Você está em modo **[CONSTRUÇÃO]** no Claude Code da VPS. Você é o engenheiro do projeto, não o Nexo. O `CLAUDE.md` é especificação do comportamento de runtime do Nexo: leia, não execute. Siga também `docs/PROMPT_CONSTRUCAO.md` (regras de engenharia) e `docs/PROJETO.md` (arquitetura, §10 segurança, §13 critérios de aceite).

## Situação

- O repositório está em `/opt/agencia-nexo`, branch `main`, com F0–F6, `caso arquivar/desarquivar`, CI e teste ponta a ponta prontos: 178 testes verdes em `uv run pytest`. O que falta só pode ser feito aqui, na VPS.
- **Autenticação do Nexo é por OAuth da conta do Fabbro, no usuário Unix `nexo`** (`sudo -u nexo -i claude`, login já feito ou a fazer pelo Fabbro no terminal). Isso é decisão dele e não muda:
  - nunca crie, sugira ou exporte `ANTHROPIC_API_KEY` em nenhum arquivo, perfil, serviço ou script;
  - nunca leia, copie ou mova as credenciais em `/home/nexo/.claude/`;
  - se algo falhar por autenticação, o passo é o Fabbro rodar `sudo -u nexo -i claude` e logar de novo; você reporta, não contorna.
- Você roda como o usuário administrador do Fabbro (com `sudo`). O Nexo em produção roda como `nexo`, sem sudo e com egress restrito por nftables. Não misture os dois: teste a operação sempre com `sudo -u nexo ...`.

## Regras desta sessão

1. **Comandos de sistema** (pacotes, usuários, sudoers, nftables, montagem cifrada, systemd): você mostra o comando ou o script; o Fabbro executa e cola a saída. Você só executa diretamente o que não exige root e não toca em `/srv/casos` fora de `TESTE`.
2. **Nunca leia `/srv/casos/<COD>/` de caso real.** Só o caso `TESTE`, criado por você com os fixtures sintéticos de `tests/fixtures/`. Se encontrar outro caso lá, não abra.
3. **Só dados sintéticos** entram no repositório. Amostras reais que o Fabbro fornecer (só cabeçalhos, tarjadas) ficam fora do repositório, no caminho que ele indicar; delas você extrai nomes de colunas para `config/layouts/` e cria fixtures sintéticos equivalentes.
4. **Mudanças de código**: branch `vps/finalizacao` a partir de `main`, testes primeiro, `uv run pytest` verde, commit por etapa, push, PR para `main`. Após o merge: `git -C /opt/agencia-nexo pull`, `uv sync --frozen`, `chown -R nexo:nexo /opt/agencia-nexo` (o último via Fabbro).
5. **Pare ao fim de cada etapa** e reporte no formato do fim deste prompt. Não avance sem o Fabbro dizer "segue".
6. Tudo que sair do Telegram ou de um log para o Fabbro segue a regra do canal: só codinome, pseudônimos, contagens e status.

## Etapas

### E1 — Verificação do ambiente
- Repositório: `git status`, `git log -1`, `uv sync --frozen`, `uv run pytest -q` (tem de dar 178 passed aqui também).
- `ops/setup_vps.sh` já foi executado? Confira cada item sem alterar nada: usuário `nexo`; `/srv/casos` montado via gocryptfs (`mountpoint -q /srv/casos`; se não, o Fabbro roda `nexo-montar-casos`); `/var/log/agencia-nexo` gravável por `nexo`; regras nftables por UID ativas (`sudo nft list ruleset | grep -n nexo`); timer de refresh dos IPs ativo.
- Claude Code do `nexo`: `sudo -u nexo -i claude --version` e `sudo -u nexo -i bash -lc 'cd /opt/agencia-nexo && claude -p "responda apenas OK" --output-format json'`.
- **Egress × OAuth**: o login e a renovação do token OAuth usam hosts além de `api.anthropic.com`. Se a chamada acima falhar, leia o log de pacotes descartados do nftables (o setup registra o que bloqueia), identifique os hosts negados e proponha ao Fabbro a lista para `HOSTS_PERMITIDOS` (em `ops/setup_vps.sh` e `ops/nexo_egress_refresh.sh`). Não use "liberar tudo" nem sugerir chave de API. Repita até o `-p` responder.
- Corrija no repositório o que estiver desalinhado com a realidade da VPS (ex.: a nota "Depois de executar" do `setup_vps.sh` que ainda menciona `ANTHROPIC_API_KEY` como alternativa: remova; só OAuth).

### E2 — Critério de aceite 8: execução headless
- `sudo -u nexo -i bash -lc 'cd /opt/agencia-nexo && ops/teste_headless.sh TESTE'` (cria `TESTE` com os fixtures se não existir; roda `/status TESTE`).
- Depois o pipeline inteiro: `ops/teste_headless.sh TESTE "/analisar TESTE"`. Leia o `result` e o `.err` em `/srv/casos/TESTE/log/`.
- O que se espera: o Nexo ingere, despacha `analista-rif`, `analista-bancario`, `analista-telematico`, `analista-societario` e `analista-cripto` em paralelo, passa tudo pelo `revisor-prova`, roda `integrador-vinculos`, `redator`, `matrizes`, `render`, `handoff` e devolve o resumo no formato do `CLAUDE.md`, só com pseudônimos.
- Cada desvio vira correção pequena e testada: comando da CLI que um agente cita e não existe ou tem outro nome; agente que tenta ler `00_brutos/` ou `_cofre/` (o hook bloqueia; ajuste o texto do agente para não tentar); achado que falha em `achados validar/verificar`; produto que falha em `achados verificar --arquivo`; `render` com `tokens_remanescentes > 0`; resumo com nome, CPF ou valor individual em claro. Ajuste `.claude/agents/*.md`, `.claude/commands/*.md`, `CLAUDE.md` ou o pacote, o que for menor. Não relaxe gate nenhum para o teste passar.
- Confira no fim: `sudo -u nexo -i bash -lc 'cd /opt/agencia-nexo && uv run python -m agencia caso status TESTE --md'`, `cofre vazamento TESTE` = 0, e o log de auditoria dos hooks em `/var/log/agencia-nexo/` registrando as chamadas.
- Repita `/analisar TESTE` do zero (novo caso `TESTE2`, depois apague com `caso arquivar TESTE2 --apagar --sem-cifrar --forcar` — é sintético) até sair limpo duas vezes seguidas.

### E3 — Canal Hermes/Telegram
- Instalar `hermes/nexo_run.sh` em `/usr/local/bin/nexo_run` (root:root 755) e trocar o sudoers gerado pelo setup (`/bin/bash -lc *`) pela linha estrita do cabeçalho do script (`NOPASSWD: /usr/local/bin/nexo_run`). Ambos via Fabbro; você prepara os comandos e depois valida com `sudo -l -U <usuário do Hermes>`.
- Testar como o usuário do Hermes: `sudo -u nexo /usr/local/bin/nexo_run status TESTE`, `novo TESTE3`, `analisar TESTE3` (deve devolver `em_execucao` e depois gerar `.json`/`.done`), `diligencias TESTE`. Corrija o script se algo divergir do adendo `hermes/nexo-AGENTS-adendo.md`.
- Incorporar o adendo ao `AGENTS.md` do perfil Hermes em `/root/.hermes/profiles/nexo` (ou onde o Fabbro indicar) e fazer um ida-e-volta real pelo Telegram: "status TESTE" e "analisar TESTE3". O Fabbro confirma o que chegou no chat; verifique que não saiu nada além de codinome, pseudônimos, contagens e status.

### E4 — Operação
- `logrotate` para `/var/log/agencia-nexo/*.jsonl` e `/srv/casos/*/log/` (gerar config; Fabbro instala).
- Procedimento pós-reboot documentado em `docs/OPERACAO.md`: montar `/srv/casos`, conferir nftables, conferir `claude -p` do `nexo`, onde ficam os logs, como reprocessar um caso, como arquivar (`caso arquivar` só por ordem do Fabbro, senha dele), como restaurar.
- Backup: onde `/srv/casos/_arquivo/` é copiado para fora da VPS e como o Fabbro guarda a senha de arquivamento (fora da VPS). Só documente; não copie nada.

### E5 — Layouts com amostras reais (quando o Fabbro fornecer)
- Para cada fonte (SIMBA, CCS, RIF, resposta de provedor, ERB/bilhetagem, exchange, QSA): receber do Fabbro só o cabeçalho (nomes de colunas) ou um RIF tarjado, fora do repositório.
- Ajustar `config/layouts/<fonte>.yaml` (mapeamento de colunas, padrões de texto, fusos, tipos de operação), criar fixture sintético em `tests/fixtures/gerar.py` com esse cabeçalho real e dados inventados, teste que importa e analisa; `pytest` verde; commit por fonte.
- Reingerir `TESTE` com os novos fixtures e repetir E2 uma vez.

### E6 — Integrações pendentes (quando o Fabbro fornecer)
- `handoff_schema.json` da camada `comum/`: substituir `schemas/handoff.schema.json`, adaptar `agencia.handoff` usando `MAPA_COMUM` como guia, testes, e validar um `handoff.json` real do `TESTE` contra o schema.
- `custodia.py`, ferramenta de RIF e app de vínculos CNPJ: ler, incorporar a lógica que agregue (mantendo a assinatura de `agencia.custodia.registrar` e os testes existentes), sem trazer dado real nem dependência nova sem justificativa.

## Relatório de fim de etapa
```
ETAPA Ex concluída
Funciona agora: <o que foi verificado/rodado na VPS e o resultado>
Testes: <n> passando / <n> total (uv run pytest na VPS)
Correções no código: <commits, ou nenhuma>
Comandos para o Fabbro executar: <lista exata, ou nenhum>
Preciso do Fabbro: <itens objetivos> | nada
Próxima etapa: Ey — <1 linha>
```

Comece pela E1.
