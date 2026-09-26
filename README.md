# Agência Nexo

Agência de análise investigativa multiagente — DRCC/DECOR/PCDF.

- Projeto: `docs/PROJETO.md`
- Runtime: Codex (`AGENTS.md`, `.codex/agents/`, `.codex/hooks.json`), como usuário `nexo` com login ChatGPT próprio
- Construção: sessões `[CONSTRUÇÃO]` seguem `docs/PROMPT_CONSTRUCAO.md`
- Operação: `nexo_run <status|novo|ingerir|analisar|diligencias> <COD>` (prompts em `comandos/`)

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
- Os hooks de `.codex/hooks.json` (`hooks/guard_paths.py`) bloqueiam comandos de shell que citam
  diretórios protegidos fora de `python -m agencia` e `apply_patch` em caminhos protegidos.
- Scripts de sistema ficam em `ops/` e `hermes/` e são **gerados, não executados** pelo engenheiro:
  `setup_vps.sh` (usuário `nexo`, gocryptfs, nftables por UID, log), `nexo_montar_casos.sh`,
  `nexo_egress_refresh.sh`, `nexo_exec.sh` (runner `codex exec`), `teste_headless.sh` (critério 8) e
  `hermes/nexo_run.sh` (ponte Telegram → Codex).
