# Validação final do Windows MCP GPT

Data de consolidação: 01/08/2026, horário de Brasília.

## Estado operacional

- Supervisor ativo como tarefa agendada do Windows.
- Um único túnel gerenciado ativo, sem duplicação.
- Servidor MCP saudável e acessível pelo túnel existente.
- Comando oficial do MCP: `D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio`.
- `mcp.connection_max_ttl` protegido em `336h`.
- Limpeza de runtime órfão restrita a processos MCP realmente abandonados.
- Timeout de PowerShell isolado por árvore de processo, sem sinal de quebra global da sessão.
- Fila, estado, heartbeat, checkpoint e evidências persistidos atomicamente.

## Evidências de validação

- Análise estática: aprovada.
- Suíte integral após integração com o upstream `v0.8.5`: `571 passed`.
- Build final: `dist/windows_mcp-0.8.5.tar.gz` e `dist/windows_mcp-0.8.5-py3-none-any.whl` gerados com sucesso.
- Log integral final: `.orquestrador/evidencias/validacao-20260801-085050941-2124-5cdc4f5a.log`.
- Resumo: `.orquestrador/evidencias/ultima-validacao.json`.
- Recuperação controlada do túnel aprovada nas tarefas `2541d09a-7fb8-4fde-9d61-b78b2dc1c40d` e `5b95fa82-30ff-45a7-885e-0313f444d89b`.
- Última recuperação aprovada: código de saída `0`, estabilidade de 60 segundos, sem duplicação de túnel e sem runtime MCP órfão.

## Observação prolongada

A janela de 280 horas foi interrompida por ordem explícita do operador em 01/08/2026. Ela acumulou 2.037 amostras, 2.027 saudáveis, 10 indisponibilidades transitórias recuperadas, nenhuma falha definitiva e nenhuma troca de PID do túnel ou do MCP. A tarefa foi marcada como `interrupted`, `resumable=false` e não será reiniciada sem nova ordem explícita.

Observações prolongadas permanecem disponíveis como tarefas configuráveis. Não constituem requisito fixo de conclusão.

## Limitação controlada

A captura de tela permanece em quarentena segura quando a sessão gráfica do Windows não permite captura isolada. A rejeição é localizada e não encerra o servidor MCP, o túnel ou as demais ferramentas. Esse comportamento é intencional e coberto por testes.

## Integração upstream

A branch foi sincronizada com `origin/main` no estado `41616c9713471c5098cde1c377ff11ae8073f767` (`v0.8.5`). Os conflitos em `src/windows_mcp/__main__.py` e `src/windows_mcp/uia/controls.py` foram resolvidos preservando as proteções locais e os novos guardas do upstream. A comunicação do worker de screenshot foi migrada de stdout para arquivo lateral JSON, eliminando risco de corrupção do protocolo STDIO.
