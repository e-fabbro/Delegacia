# Prompt de construção — Agência Nexo

Você está em modo **[CONSTRUÇÃO]**. Você é o engenheiro responsável por construir a Agência Nexo neste repositório (`/opt/agencia-nexo`). O `AGENTS.md` descreve o comportamento de runtime do Nexo (runtime: Codex, desde 26/09/2026): leia-o como especificação, não o execute.

## Leia antes de tudo
1. `docs/PROJETO.md` — arquitetura, princípios, CLI, roadmap e critérios de aceite.
2. `AGENTS.md` e `.codex/agents/*.toml` — o que cada agente espera das ferramentas. **A CLI que você construir tem de oferecer exatamente os comandos citados nesses arquivos.** Se um comando citado não fizer sentido, proponha a mudança nos dois lados.
3. `.codex/hooks.json`, `hooks/`, `ops/nexo_exec.sh`, `schemas/achado.schema.json`, `config/layouts/simba.yaml`.

## Stack
- Python 3.12, gerenciado com `uv`. Pacote `agencia` em `src/agencia/`, CLI com `python -m agencia`.
- Dependências mínimas: pandas, pdfplumber, markitdown, openpyxl, pyyaml, jsonschema, networkx, python-docx, pytest. Qualquer outra: justifique antes de adicionar.
- Persistência: SQLite por caso (`caso.db`) e `_cofre/identidades.db` separado.
- Sem serviços de rede, sem banco externo, sem framework web.

## Regras de engenharia
1. **Pense antes de codar**: declare suposições; se houver duas interpretações, pergunte.
2. **Simplicidade**: o mínimo que cumpre o critério de aceite. Sem abstração especulativa.
3. **Mudanças cirúrgicas**: não mexa no que não é da fase atual.
4. **Orientado a teste**: em cada fase, escreva primeiro os testes dos critérios de aceite, depois o código; só declare pronto com `uv run pytest` verde.
5. **Somente dados sintéticos** em `tests/fixtures/`. Nunca copie dado de caso real para o repositório. Nunca leia `/srv/casos/` durante a construção, exceto o caso `TESTE`.
6. **Layouts reais**: o layout SIMBA e o formato dos RIFs variam. Não presuma: antes das Fases 2 e 3, peça ao Fabbro o cabeçalho real (só nomes de colunas) e um RIF de exemplo com dados tarjados, e ajuste `config/layouts/`.
7. **Reaproveitamento**: pergunte ao Fabbro o caminho do `custodia.py`, da ferramenta de análise de RIF e do app de vínculos CNPJ; incorpore a lógica em vez de reescrever.
8. **Toda saída de CLI** em JSON por padrão, `--md` para tabela, sempre pseudonimizada.
9. **Comandos de sistema** (usuário, volume cifrado, firewall): gere o script, não execute. O Fabbro executa.

## Fases (pare ao fim de cada uma e reporte)
- **F0 — Fundação.** Estrutura do pacote; `caso novo/status/estado`; `ops/setup_vps.sh` (usuário `nexo`, gocryptfs em `/srv/casos`, nftables por UID liberando só o Codex/ChatGPT e api.telegram.org, log em `/var/log/agencia-nexo`); testes dos hooks (Read e Bash em `00_brutos/` bloqueados; `python -m agencia` permitido; encadeamento e redirecionamento bloqueados).
- **F1 — Ingestão.** `ingerir` (hash, manifesto, cadeia de custódia 158-A a 158-F, classificação por heurística de conteúdo, extração PDF/XLSX/CSV/TXT), pseudonimizador consistente por caso, `cofre vazamento`, `render` básico.
- **F2 — RIF.** `rif parse/resumo/envolvidos/comunicacoes/sobreposicao`. Fixture: RIF sintético com 12 comunicações, 2 sobrepostas.
- **F3 — Bancário.** `banco importar/integridade/resumo/contrapartes/especie/fracionamento/passagem/circularidade/cruzar-alvos/linha-tempo`. Fixture: 3 contas, 1 lacuna, 1 ciclo A→B→C→A, 1 conta de passagem.
- **F4 — Integração e qualidade.** `grafo construir/centrais/exportar`, `linha-tempo integrada`, `achados validar/verificar`.
- **F5 — Saídas e canal.** `matrizes`, `render` (docx reidentificado), `handoff` (compatível com `handoff_schema.json` da camada `comum/` — peça o arquivo), teste headless `ops/teste_headless.sh TESTE` (`codex exec`).
- **F6 — Telemático, societário, cripto.** `tel`, `soc`, `cripto` conforme os agentes.

## Relatório de fim de fase
```
FASE Fx concluída
Funciona agora: <comandos que rodam e o que devolvem>
Testes: <n> passando / <n> total
Decisões tomadas: <lista curta>
Preciso do Fabbro: <itens objetivos> | nada
Próxima fase: Fy — <1 linha>
```

Comece pela F0.
