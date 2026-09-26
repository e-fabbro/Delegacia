# Agência Nexo

Agência de análise investigativa multiagente — DRCC/DECOR/PCDF.

- Projeto: `docs/PROJETO.md`
- Construção: `claude "[CONSTRUÇÃO] Leia docs/PROMPT_CONSTRUCAO.md e execute a Fase 0."`
- Operação: `/caso-novo`, `/ingerir`, `/analisar`, `/status`, `/diligencias`

## Desenvolvimento

CI: `.github/workflows/testes.yml` roda `uv sync --frozen`, regenera os fixtures sintéticos e executa `pytest` a cada push e PR. `tests/test_pipeline.py` percorre o caso inteiro (ingestão → análises → achados → grafo → produto → matrizes → render → handoff → arquivamento).

```bash
uv sync                      # Python 3.12 + dependências (pyproject.toml / uv.lock)
uv run pytest                # testes (só dados sintéticos em tests/fixtures/)
uv run python -m agencia -h  # CLI
```

- A raiz dos casos é `/srv/casos`; para desenvolvimento e testes use `AGENCIA_CASOS=<dir>`.
- Fim do caso: `caso arquivar <COD> --apagar` com a senha em `AGENCIA_ARQUIVO_SENHA` (pacote cifrado em `_arquivo/`); `caso desarquivar` restaura.
- Layouts das fontes (SIMBA, CCS, RIF) em `config/layouts/*.yaml`; `AGENCIA_LAYOUTS=<dir>` troca o diretório.
- Fixtures sintéticos: `uv run python tests/fixtures/gerar.py` (RIF de 12 comunicações, SIMBA de 3 contas, CCS, registros
  telemáticos em UTC, ERB em horário local, QSA de 3 PJ, extrato de exchange).
- Os hooks de `.claude/settings.json` valem também durante a construção: comandos Bash que
  citam diretórios protegidos são bloqueados fora de `python -m agencia`; use as ferramentas
  Write/Edit para gravar código que precise citá-los.
- Scripts de sistema ficam em `ops/` e `hermes/` e são **gerados, não executados** pelo engenheiro:
  `setup_vps.sh` (usuário `nexo`, gocryptfs, nftables por UID, log), `nexo_montar_casos.sh`,
  `nexo_egress_refresh.sh`, `teste_headless.sh` (critério 8, `claude -p "/status TESTE"`) e
  `hermes/nexo_run.sh` (ponte Telegram → Claude Code).
