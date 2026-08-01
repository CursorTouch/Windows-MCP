# Matriz requisito–evidência

| Requisito | Estado | Evidência objetiva |
|---|---|---|
| Negociação MCP e listagem de ferramentas | Aprovado | Suítes de integração e registros em `.orquestrador/evidencias` |
| Rejeição isolada sem queda da sessão | Aprovado | Testes de isolamento e `SafetyDryRun` |
| Supervisor persistente | Aprovado | Tarefa `Windows-MCP-GPT-Supervisor`, heartbeat e `state.json` |
| Túnel único | Aprovado | Heartbeat com um PID de túnel e testes contra duplicação |
| Runtime MCP saudável | Aprovado | `Health`, `/healthz`, `/readyz` e verificação do comando oficial |
| TTL para sessões prolongadas | Aprovado | Perfil e guarda do Supervisor com `connection_max_ttl=336h` |
| Recuperação automática | Aprovado | Tarefas `2541d09a-7fb8-4fde-9d61-b78b2dc1c40d` e `5b95fa82-30ff-45a7-885e-0313f444d89b` |
| Retomada de tarefas | Aprovado | Testes de `recover_queue` e persistência de resultados |
| Persistência atômica | Aprovado | Retentativas de compartilhamento, fsync, replace atômico e testes |
| Limpeza de órfãos | Aprovado | Classificação por descendência e teste de recuperação real |
| Timeout do PowerShell | Aprovado | `tests/test_powershell_timeout_isolation.py` |
| Análise estática | Aprovado | `ruff` em `.orquestrador/evidencias/ultima-validacao.json` |
| Regressão integral | Aprovado | `462 passed` em `validacao-20260801-082441258-24512-898ad82d.log` |
| Build reproduzível | Aprovado | Wheel e sdist gerados pelo `uv build` |
| Observação de 280 horas | Interrompida pelo operador | `.orquestrador/evidencias/observation/interrupted-280h-confirmed-20260801-081613.json` |
| Captura de tela | Quarentena segura | Testes de quarentena; falha localizada sem derrubar a sessão |
| Documentação operacional | Aprovado | `docs/resilience-operations.md` e `docs/final-validation.md` |
