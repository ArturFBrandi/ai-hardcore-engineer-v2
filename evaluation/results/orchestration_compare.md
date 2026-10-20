# Orchestration comparison (MiniMax-M3)

| mode | question | intent | latency s | LLM calls | tokens | USD | supervisor steps | model filter | sources | status |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---|
| supervisor | My card machine won't connect to the internet | technical_support | 33.87 | 11 | 20791 | 0.008047 | 3 | True | 2 | answered |
| supervisor | Minha maquininha não conecta na internet, o q | technical_support | 18.61 | 7 | 25681 | 0.009081 | 1 | True | 1 | answered |
| supervisor | Minha Get Smart está sem sinal desde cedo | technical_support | 36.51 | 9 | 16920 | 0.006373 | 3 | True | 2 | answered |
| supervisor | When will the money from yesterday's sales be | customer_settlement | 23.36 | 5 | 13609 | 0.005148 | 0 | False | 2 | answered |
| supervisor | Quando vai cair o dinheiro das vendas de onte | customer_settlement | 16.38 | 5 | 14085 | 0.005413 | 0 | False | 1 | answered |
| supervisor | Minha maquininha está mostrando um erro de tr | customer_transaction | 59.19 | 8 | 16781 | 0.006383 | 3 | False | 1 | answered |
| static | My card machine won't connect to the internet | technical_support | 20.58 | 7 | 27104 | 0.009721 | 0 | False | 4 | answered |
| static | Minha maquininha não conecta na internet, o q | technical_support | 13.14 | 6 | 19466 | 0.007111 | 0 | False | 4 | answered |
| static | Minha Get Smart está sem sinal desde cedo | technical_support | 16.7 | 6 | 13672 | 0.005093 | 0 | False | 0 | awaiting_human |
| static | When will the money from yesterday's sales be | customer_settlement | 8.53 | 5 | 12074 | 0.004501 | 0 | False | 2 | answered |
| static | Quando vai cair o dinheiro das vendas de onte | customer_settlement | 11.93 | 5 | 12879 | 0.004972 | 0 | False | 3 | answered |
| static | Minha maquininha está mostrando um erro de tr | technical_support | 15.86 | 5 | 13473 | 0.005246 | 0 | False | 3 | answered |

| mode | mean latency s | mean LLM calls | mean tokens | mean USD | troubleshooting answers with model filter |
|---|---:|---:|---:|---:|---:|
| supervisor | 31.32 | 7.50 | 17978 | 0.00674 | 3/3 |
| static | 14.46 | 5.67 | 16445 | 0.00611 | 0/4 |
