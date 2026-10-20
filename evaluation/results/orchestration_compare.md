# Orchestration comparison (fake LLM)

| mode | question | intent | latency s | LLM calls | tokens | USD | supervisor steps | model filter | sources | status |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---|
| supervisor | My card machine won't connect to the internet | technical_support | 1.51 | 6 | 8061 | 0.003111 | 0 | True | 1 | answered |
| supervisor | Minha maquininha não conecta na internet, o q | technical_support | 1.31 | 6 | 8026 | 0.003086 | 0 | True | 1 | answered |
| supervisor | Minha Get Smart está sem sinal desde cedo | technical_support | 2.42 | 7 | 8671 | 0.003203 | 1 | True | 0 | awaiting_human |
| supervisor | When will the money from yesterday's sales be | customer_settlement | 1.19 | 5 | 8007 | 0.003269 | 0 | False | 2 | answered |
| supervisor | Quando vai cair o dinheiro das vendas de onte | customer_settlement | 1.22 | 5 | 7997 | 0.003263 | 0 | False | 2 | answered |
| supervisor | Minha maquininha está mostrando um erro de tr | customer_transaction | 1.29 | 5 | 6162 | 0.002307 | 1 | False | 1 | answered |
| static | My card machine won't connect to the internet | technical_support | 1.56 | 6 | 8604 | 0.003487 | 0 | False | 2 | answered |
| static | Minha maquininha não conecta na internet, o q | technical_support | 1.33 | 6 | 8834 | 0.003529 | 0 | False | 2 | answered |
| static | Minha Get Smart está sem sinal desde cedo | technical_support | 1.41 | 6 | 7807 | 0.002879 | 0 | False | 0 | awaiting_human |
| static | When will the money from yesterday's sales be | customer_settlement | 1.35 | 5 | 8007 | 0.003269 | 0 | False | 2 | answered |
| static | Quando vai cair o dinheiro das vendas de onte | customer_settlement | 1.35 | 5 | 7997 | 0.003263 | 0 | False | 2 | answered |
| static | Minha maquininha está mostrando um erro de tr | customer_transaction | 0.12 | 4 | 4805 | 0.001674 | 0 | False | 0 | answered |

| mode | mean latency s | mean LLM calls | mean tokens | mean USD | troubleshooting answers with model filter |
|---|---:|---:|---:|---:|---:|
| supervisor | 1.49 | 5.67 | 7821 | 0.00304 | 3/3 |
| static | 1.19 | 5.33 | 7676 | 0.00302 | 0/3 |
