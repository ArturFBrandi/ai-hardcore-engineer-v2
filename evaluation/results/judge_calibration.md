# LLM judge calibration (probabilistic — not a CI gate)

Judge: MiniMax-M3, temperature 0, separate prompt (`app/agents/prompts/judge.md`). Items judged: 30/30.
Labels: Answer/label pairs for judge calibration. Labels were DRAFTED (constructed faithful vs. planted-error answers) by draft:claude-opus-5.5 on 2026-10-19 and require human confirmation (see golden/README.md).

**Accuracy vs labels: 1.000 · Cohen's κ: 1.000 (strong agreement).**

Use: the judge may be consulted for semantic groundedness in exploratory live runs. Deterministic metrics (claim recall, hallucination traps, citation faithfulness) remain the gates.

| golden | label | judge | answer |
|---|---|---|---|
| g011 | True | True | A Getnet permite parcelar em até 48x no crediário; o prazo máximo e os juros dependem do e |
| g011 | False | False | No crediário você pode parcelar em até 12x sem juros, definido pela Getnet. |
| g013 | True | True | Yes. The Payment Link can be sent through WhatsApp, as well as social networks, e-mail and |
| g013 | False | False | No, the Payment Link only works by e-mail. |
| g019 | True | True | Charge the Get Smart for at least 3 hours in a row and recharge when the battery is around |
| g019 | False | False | Always let the battery drain to 0% before charging it for 30 minutes. |
| g022 | True | True | Get Tap needs an Android 12 (or newer) phone with NFC. |
| g022 | False | False | Get Tap works on any iPhone with iOS 10. |
| g023 | True | True | O Get Tap não tem mensalidade; você paga apenas as taxas por transação. |
| g023 | False | False | O Get Tap custa R$ 49,90 por mês. |
| g024 | True | True | Não existe limite pré-determinado, mas acima de R$ 200 o cliente pode precisar digitar a s |
| g024 | False | False | O limite por venda no Get Tap é de R$ 1.000. |
| g034 | True | True | O estorno na maquininha só pode ser feito no mesmo dia, com a senha do lojista (padrão 000 |
| g034 | False | False | Você pode estornar vendas de até 90 dias pela maquininha, sem senha. |
| g037 | True | True | Há o Parcelado Emissor (até 24 parcelas) e o Parcelado Lojista (até 12 parcelas). |
| g037 | False | False | A Getnet só oferece o Parcelado Lojista, em até 36 parcelas. |
| g038 | True | True | Pre-authorization reserves the amount on the customer's card; it must be confirmed within  |
| g038 | False | False | Pre-authorization charges the card immediately and cannot be changed. |
| g039 | True | True | No Plano Light você paga 12 parcelas e depois tem 100% de desconto; a parcela é R$ 12,90 s |
| g039 | False | False | O Plano Light custa R$ 99,90 por mês para sempre. |
| g045 | True | True | O erro ADQ 4-83 é um erro de integração com as bandeiras: tecla Função, número 38, Inicial |
| g045 | False | False | O erro ADQ 4-83 indica bateria fraca; troque o carregador. |
| g054 | True | True | Não é preciso ter site; no campo url informe https://minhaconta.getnet.com.br. |
| g054 | False | False | Sim, é obrigatório ter um site com certificado SSL próprio. |
| g056 | True | True | A habilitação do Link de Pagamento ocorre no mesmo dia. |
| g056 | False | False | A habilitação do Link de Pagamento leva 30 dias úteis. |
| g058 | True | True | No, Pix through Get Conta is free. |
| g058 | False | False | Pix through Get Conta costs 0.99% per transaction. |
| g064 | True | True | A Get Clássica tem 1 ano de garantia e a bateria dura entre 4h e 8h. |
| g064 | False | False | A Get Clássica tem 5 anos de garantia e bateria de 48h. |
