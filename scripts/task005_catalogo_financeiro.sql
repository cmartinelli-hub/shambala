-- TASK_005 — somente leitura. Execute dentro de BEGIN READ ONLY no banco alvo.
-- Não contém nem imprime dados pessoais ou valores individualizados.
SELECT c.relname AS tabela, a.attname AS coluna, pg_catalog.format_type(a.atttypid, a.atttypmod) AS tipo,
       a.attnotnull AS obrigatoria, pg_get_expr(ad.adbin, ad.adrelid) AS padrao
  FROM pg_class c
  JOIN pg_attribute a ON a.attrelid = c.oid
  LEFT JOIN pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum
 WHERE c.relname IN ('contas_financeiras','financeiro_movimentacoes','vendas_pdv',
                     'vendas_pdv_itens','fiado_credito_mov','caixa_movimentos',
                     'caixa_sangrias','produtos','estoque_movimentos','vendas_livros')
   AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY c.relname, a.attnum;

SELECT c.relname AS tabela, co.conname, co.contype, pg_get_constraintdef(co.oid) AS definicao
  FROM pg_constraint co JOIN pg_class c ON c.oid = co.conrelid
 WHERE c.relname IN ('contas_financeiras','financeiro_movimentacoes','vendas_pdv',
                     'vendas_pdv_itens','fiado_credito_mov','caixa_movimentos',
                     'caixa_sangrias','produtos','estoque_movimentos','vendas_livros')
 ORDER BY c.relname, co.conname;

SELECT tablename AS tabela, indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = 'public'
   AND tablename IN ('contas_financeiras','financeiro_movimentacoes','vendas_pdv',
                     'vendas_pdv_itens','fiado_credito_mov','caixa_movimentos',
                     'caixa_sangrias','produtos','estoque_movimentos','vendas_livros')
 ORDER BY tablename, indexname;

SELECT event_object_table AS tabela, trigger_name, event_manipulation
  FROM information_schema.triggers
 WHERE event_object_schema='public'
 ORDER BY event_object_table, trigger_name;
