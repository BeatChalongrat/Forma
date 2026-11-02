# Academic / research-lab literature on LLM schema matching, table understanding and data wrangling (2022–2026)

Scope note: this covers the research side only (arXiv, VLDB/SIGMOD/ICDE/NeurIPS/ICML/ACL, Microsoft Research, NYU VIDA, Cambridge, Zhejiang, Megagon-adjacent work). Vendor products are covered by other researchers. Every number below is what the paper itself reports (or a clearly marked secondary summary). Where I could not open the primary text, the gap is stated.

Correction to the brief: **Matchmaker is NOT Microsoft Research** — it is Nabeel Seedat & Mihaela van der Schaar, University of Cambridge, NeurIPS 2024 ([arXiv 2410.24105](https://arxiv.org/abs/2410.24105); [NeurIPS page](https://neurips.cc/virtual/2024/106882)). Jellyfish is NOT Megagon — it is Osaka University / NEC / Nagoya ([arXiv 2312.01678](https://arxiv.org/abs/2312.01678)). Microsoft Research's schema-matching work is LSM (ICDE 2023) and Table-GPT (SIGMOD 2024); Megagon's is Ditto (VLDB 2021).

---

## KQ0. Catalogue: the systems, their architectures and headline numbers on standard benchmarks

### Takeaway
Since 2024 the field has converged on one architecture — *retrieve candidates (embeddings or a small fine-tuned model) → LLM rerank/score a shortlist → optional confidence/abstention* — and on two healthcare benchmarks (MIMIC-III→OMOP, Synthea→OMOP) where the best acc@1 is still only ~62–70%, i.e. roughly one in three top-1 matches is wrong without a human.

### Cited Findings

**Benchmarks**
- MIMIC-III→OMOP (introduced by ReMatch): source 268 columns / 25 tables, 156 mapped columns, 112 null mappings; target OMOP 425 columns / 38 tables. Synthea→OMOP (from the OMAP project): source 38 columns / 8 tables, target 67 columns / 8 tables. Ground truth for MIMIC was produced manually by one domain expert — [ReMatch, arXiv 2403.01567, 2024](https://arxiv.org/html/2403.01567v2)
- Parciak et al.'s health benchmark uses MIMIC-IV→OMOP: 99 table pairs, 1,839 attribute pairs, only 49 true 1:1 matches — [Schema Matching with LLMs: an Experimental Study, VLDB 2024 TaDA workshop](https://arxiv.org/html/2407.11852v1); [TaDA proceedings PDF](https://vldb.org/workshops/2024/proceedings/TaDA/TaDA.8.pdf)
- GDC benchmark (Magneto): built with biomedical researchers from ten tumour-analysis studies, manually aligned to the NCI Genomic Data Commons model; source tables 16–179 columns, 93–225 rows; simplified target has 736 columns; some columns have up to 4,478 distinct values; ground truth by annotator consensus — [Magneto, PVLDB 18(8) 2025](https://arxiv.org/html/2412.08194v2); [VLDB page](https://vldb.org/pvldb/volumes/18/paper/Magneto%3A%20Combining%20Small%20and%20Large%20Language%20Models%20for%20Schema%20Matching)
- Valentine (ICDE 2021, Koutras et al.): open-source suite with eight seminal matchers — schema-based (Cupid, Similarity Flooding, COMA-Schema) and instance-based (Distribution-based, COMA-Instance, Jaccard-Levenshtein); fabricated dataset pairs with controlled noise — [Valentine, arXiv 2010.07386](https://arxiv.org/abs/2010.07386); [TU Delft record](https://repository.tudelft.nl/record/uuid:1335cd61-8076-4327-923a-12ea5cfb6eed)
- LakeBench (PVLDB 17, 2024, Deng/Chai et al.): join & union search benchmark over >16M real tables (1,600× larger than prior), >10k labelled queries; implementations of TUS, D3L, Santos, Starmie, DeepJoin — [PVLDB p1925](https://vldb.org/pvldb/vol17/p1925-chai.pdf); [GitHub RLGen/LakeBench](https://github.com/RLGen/LakeBench). A distinct 2024 "LakeBench" by Khatiwada et al. introduces 8 benchmarks for union/join/subset and fine-tunes TabSketchFM, reporting up to 55% F1 gain over other neural methods — [arXiv 2407.01619](https://arxiv.org/pdf/2407.01619v1)
- LLMatch introduces SchemaNet, derived from real schema pairs across three enterprise domains, for multi-table alignment (no numbers on the abstract page) — [LLMatch, arXiv 2507.10897, 2025](https://arxiv.org/abs/2507.10897)

**Systems and reported results**
- **ReMatch (2024)** — serialises target tables and source attributes as structured "documents" (table name as title, purpose paragraph, PK/FK, each attribute + type + description); Ada-002 embeddings retrieve top-J candidate target *tables*; GPT-4-1106 ranks top-K attributes inside them. Best MIMIC: acc@1 0.424, acc@5 0.729 (J=2); Synthea: acc@1 0.562, acc@5 0.924 (no retrieval, J=∞, because the target has only 8 tables). Without retrieval on MIMIC: acc@1 falls to 0.311, acc@5 to 0.518. Names-only (drop descriptions): acc@1 0.396, acc@5 0.503. Adding one known mapping per table as "guidance" raised MIMIC acc@1 to 0.539 (+20.4 pts) — [ReMatch](https://arxiv.org/html/2403.01567v2)
- **SMAT (2021, supervised deep model)** as baseline: MIMIC 80-20 split acc@1 0.454 / acc@5 1.000 / F1 0.102; realistic 20-80 split acc@1 0.077 / acc@5 0.361 / F1 0.084; Synthea 20-80 acc@1 0.271 — [ReMatch Table 6](https://arxiv.org/html/2403.01567v2)
- **Matchmaker (NeurIPS 2024)** — Step 1 ColBERT-v2 multi-vector index of target attributes; Step 2 candidate generation from two sources (semantic retrieval top-k + LLM chain-of-thought "reasoning-based" candidates) then an LLM refiner narrows the union; Step 3 confidence scoring as a multiple-choice question with a "NONE of the above" abstention option, LLM gives 0–100 per option. Results (GPT-4 backbone for all LLM baselines): MIMIC acc@1 62.20±2.40 vs ReMatch 42.50, LLM-DP 29.59, Jellyfish-13B 15.36, SMAT(20-80) 6.05; acc@5 71.10 vs ReMatch 72.90 (ReMatch wins at @5). Synthea acc@1 70.20±1.70 vs ReMatch 50.50, LLM-DP 41.44, Jellyfish-13B 35.17, SMAT(50-50) 44.88; acc@5 80.90 vs 74.30. With GPT-3.5 Matchmaker drops to acc@1 48.30 (MIMIC) / 47.80 (Synthea). LLM calls: Matchmaker 1,340 (MIMIC) / 890 (Synthea) vs ReMatch 268/178 vs pairwise binary classifiers 24,771/29,637 — [Matchmaker](https://arxiv.org/html/2410.24105v1)
- **Schemora (2025, preprint)** — hybrid vector + BM25 lexical retrieval plus LLM metadata enrichment, no labels; claims +7.49% HitRate@5 and +3.75% HitRate@3 over prior best on MIMIC-OMOP (absolute numbers not on abstract page) — [Schemora, arXiv 2507.14376](https://arxiv.org/abs/2507.14376)
- **Magneto (PVLDB 2025, NYU)** — SLM (MPNet) retrieves/ranks candidates by cosine; SLM is fine-tuned self-supervised with triplet loss on LLM-generated synonym/value variants of *target* columns only; LLM (GPT-4o-mini) reranks top-k with a 0.00–1.00 scoring prompt (one-shot, fixed format); alternative bipartite-graph reranker needs no LLM. GDC: Magneto-ft-llm with LLM augmentation MRR 0.866; MPNet-ft + LLaMA-3.3-70B reranker MRR 0.860; zero-shot GPT-4o-mini default serialization MRR 0.775; at k=5 LLM rerank vs bipartite: MRR 0.731→0.780, Recall@GT 0.375→0.475. Latency on GDC: zs-bp 11–33 s, zs-llm 545–589 s; reranking against the whole schema with GPT-4o >6,000 s per table; ISResMat did not finish, Unicorn finished only the first 100 columns. On Valentine, Magneto ≥ baselines except Unicorn (which they suspect trained on Valentine) — [Magneto](https://arxiv.org/html/2412.08194v2)
- **KcMF (Zhejiang, 2024/25)** — fine-tuning-free; pseudo-code task decomposition (expert-written if-then-else rules in NL), Dataset-as-Knowledge (mine metadata from the candidate pool), Example-as-Knowledge (keywords → SNOMED-CT etc.), demonstration summarisation, and majority-vote ensemble over knowledge sources (IntGE). Schema matching F1: GPT-3.5 MIMIC 0.0745→0.6207, Synthea 0.0909→0.4242, CMS 0.0196→0.2917; GPT-4o MIMIC 0.3030→0.3500, Synthea 0.0769→0.4667, CMS 0.1667→0.4082; SMAT 0.2020/0.3850/0.5000; Jellyfish fine-tuned 0.4314 on MIMIC. Abstract v2 says +17.93 avg F1 over non-LLM SOTA (v1 said 22.9%). Cost ≈ 60 US cents per dataset — [KcMF, arXiv 2410.12480](https://arxiv.org/html/2410.12480v2)
- **Jellyfish (Osaka/NEC, 2023–24)** — Llama-2-13B (also 7B/8B) instruction-tuned on ~115k instances across error detection, imputation, schema matching, entity matching; prompt = system + task description + injected knowledge + instance + question + output format; reasoning data from Mixtral-8x7B. Schema matching F1: Jellyfish-7B/8B/13B MIMIC 53.33/45.45/40.00, Synthea 55.56/47.06/56.00, CMS (unseen) 42.86/38.10/59.29; GPT-4 40.00/66.67/19.35; SMAT 20.00/38.50/50.00. Knowledge injection on unseen CMS: 7.14→59.29. Entity matching Jellyfish-13B: Amazon-Google 81.34, DBLP-ACM 98.98, Fodors-Zagats 100, Abt-Buy (unseen) 89.58 — [Jellyfish v5](https://arxiv.org/html/2312.01678v5)
- **Unicorn (SIGMOD 2023, Tsinghua/RUC)** — one encoder + mixture-of-experts + binary matcher trained over 7 matching tasks / 20 datasets; schema matching uses Valentine's FabricatedDatasets; claims better than task-specific SOTA on most tasks — [GitHub ruc-datalab/Unicorn](https://github.com/ruc-datalab/Unicorn); [PDF](https://dbgroup.cs.tsinghua.edu.cn/ligl/papers/Unicorn_PACMMOD.pdf) (PDF not parseable here; per-task numbers not extracted)
- **LSM (Microsoft, ICDE 2023, Zhang, Floratou, Cahoon et al.)** — "Learned Schema Mapper": fine-tuned pre-trained LM for *data-free* schema matching (metadata only, motivated by privacy); authors state prior linguistic matchers had low accuracy on real Microsoft-customer schemata — [Microsoft Research page](https://www.microsoft.com/en-us/research/?p=909858); [DBLP](https://dblp.uni-trier.de/rec/conf/icde/ZhangFCKMBPP23.html). A search summary describes LSM as human-in-the-loop with active learning and targeted feedback to cut labelling cost, but I could not open the PDF to verify.
- **Table-GPT (Microsoft, SIGMOD 2024)** — "table-tuning" GPT-3.5/ChatGPT on synthesised table tasks. Schema matching on DeepM: ChatGPT zero-shot 0.857 → 1.0 after table-tuning; column type annotation (held-out task) Limaye zero-shot 0.683→0.755, Sherlock 0.332→0.449 — [Table-GPT, arXiv 2310.09263](https://arxiv.org/pdf/2310.09263); [HF dataset README](https://huggingface.co/datasets/LipengCS/Table-GPT/blob/main/README.md)
- **Column Type Annotation using ChatGPT (Korini & Bizer 2023)** — with instructions plus a two-step pipeline ChatGPT reaches F1 > 85% zero/one-shot — [arXiv 2306.00745](https://arxiv.org/abs/2306.00745)
- **TableGPT2 (Zhejiang, 2024)** — pre-trained/fine-tuned on 593.8K tables, 2.36M query-table-output tuples; claims +35.20% avg (7B) and +49.32% (72B) over "comparable" LLMs across 23 metrics / 6 tasks; baselines not named in snippets — [arXiv 2411.02059](https://www.arxiv.org/pdf/2411.02059); [HF model card](https://huggingface.co/tablegpt/TableGPT2-7B). TableLlama = Llama-2-7B + LongLoRA on TableInstruct (no head-to-head numbers found)
- **Ditto (Megagon/VLDB 2021, comparator for entity matching)** — pre-trained Transformer fine-tuned for EM; up to +29% F1 over prior SOTA (arXiv v1 says 19%, another copy 31%); F1 96.5% matching two company datasets of 789K and 412K records — [Ditto, arXiv 2004.00584](https://arxiv.org/abs/2004.00584v1)
- **OmniMatch (TU Delft, 2024 → VLDB 2025 as "Joinability Discovery in Data Products")** — graph of column-pair similarity signals + R-GCN trained on auto-generated positive/negative join examples; up to +14% F1 and PR-AUC over SOTA matchers without per-metric thresholds — [arXiv 2403.07653](https://arxiv.org/abs/2403.07653v1)
- **Prompt-Matcher (2024/25)** — uses GPT-4 to verify correspondences in a *probabilistic* candidate result set under a token budget; greedy (1−1/e) selection (problem shown NP-hard); two prompts, Semantic-Match and Abbreviation-Match (adds domain context, COMA-style abbreviation rules, value-overlap cues). Accuracy: Semantic-Match 100% DeepMDatasets / 70.0% Valentine-Fabricated; Abbreviation-Match 99.35% / 91.8%; the abbreviation tips alone lift Fabricated 70→91.8 — [Prompt-Matcher, arXiv 2408.14507](https://arxiv.org/html/2408.14507v3)
- **SemStruct (KDD'26 submission)** — frozen E5/MPNet + lightweight GraphSAGE over column/value/row nodes, self-supervised; SOTA MRR/Recall on Valentine and SOTAB-SM vs Magneto, ISResMat, REMA (numbers only in figures) — [alphaXiv 2605.30729](https://www.alphaxiv.org/abs/2605.30729)
- **ConStruM (Chen, Zhang, Jagadish; 2026 preprint)** — add-on to any upstream matcher: a context tree for budgeted retrieval plus a similarity hypergraph that groups near-duplicate columns and emits "differentiation cues" into the final LLM prompt; search summary cites 0.935 accuracy vs 0.503 for ReMatch on one dataset — [arXiv 2601.20482](https://arxiv.org/abs/2601.20482)
- **LLMatch (2025)** — three stages (schema preparation → table-candidate selection → column-level alignment) with Rollup (group columns into concepts) / Drilldown (re-expand); introduces SchemaNet enterprise benchmark; only qualitative claims on abstract page — [arXiv 2507.10897](https://arxiv.org/abs/2507.10897)

### Inferences
- Across ReMatch → Matchmaker → Schemora, acc@1 on MIMIC-OMOP moved ~42 → 62 → (62 + a few points); the ceiling appears to be the benchmark's own noise (single-annotator ground truth, 112 "null" columns), not just the models.
- Baseline comparisons are inconsistent: the same SMAT/Jellyfish/GPT-4 get very different numbers across KcMF, Jellyfish and Matchmaker papers because metrics differ (F1 over pairwise decisions vs acc@k over a ranked list) and because prompt/serialisation differ. Treat cross-paper comparisons as indicative only.
- Fine-tuned small models (Jellyfish-7B) can beat GPT-4 on MIMIC F1 but collapse on *unseen* schemas unless knowledge is injected (CMS 7.14 → 59.29) — supervised matchers do not transfer across customers; retrieval + prompting does.

### Gaps
- Unicorn's per-task schema-matching F1 and LSM's active-learning numbers: PDFs were not parseable; abstracts give no figures.
- "LLM-SM" as a named system was not found; it may be an informal label for Parciak et al. or a generic phrase.
- Schemora, LLMatch, ConStruM, SemStruct: full result tables not read (only abstract pages / figures).
- No WikiTables-based *schema matching* number was found; WikiTQ numbers appear only for table QA (KQ4).

---

## KQ1. Which techniques give the largest accuracy gains in LLM schema matching, with numbers?

### Takeaway
The largest, repeatedly measured gains come from (a) narrowing the candidate set before the LLM decides (retrieval or candidate generation: +10–20 acc@1 points), (b) giving the LLM descriptive context about each attribute (names-only costs ~3–23 points), (c) structured task decomposition / domain rules / abbreviation hints (+20 to +50 F1 on weak backbones), and (d) one known anchor mapping per table (+20 acc@1). Self-generated demonstrations help modestly (+4–5 points); plain self-reflection does not.

### Cited Findings
- **Retrieval / candidate narrowing**: ReMatch MIMIC acc@1 0.424 with top-2 tables vs 0.311 with the whole target (38 tables); acc@5 0.729 vs 0.518. More tables (J=7) also hurts: acc@1 0.317 — [ReMatch](https://arxiv.org/html/2403.01567v2). Parciak et al.: 1-to-1 prompts (no context) F1 0.241 vs 1-to-N 0.398 / N-to-1 0.388 (GPT-3.5); GPT-4 1-to-N 0.518, N-to-1 0.580, N-to-M 0.469 — "too little and too much context both hurt" — [Parciak et al. 2024](https://arxiv.org/html/2407.11852v1)
- **Two candidate sources > one**: Matchmaker full 62.20 MIMIC vs semantic-only 60.20 vs reasoning-only 61.60; on Synthea reasoning-only (73.00) actually beat full (70.20), a case where retrieval candidates hurt — [Matchmaker](https://arxiv.org/html/2410.24105v1)
- **Descriptions matter**: ReMatch names-only MIMIC acc@1 0.396 vs 0.424 and acc@5 0.503 vs 0.729 — [ReMatch](https://arxiv.org/html/2403.01567v2). Schemora's gain is attributed to metadata enrichment + hybrid lexical/vector retrieval — [Schemora](https://arxiv.org/abs/2507.14376)
- **Anchor mapping (relevance feedback)**: one known mapping per table raised ReMatch MIMIC acc@1 0.424→0.539 and acc@5 to 0.783 — [ReMatch](https://arxiv.org/html/2403.01567v2)
- **Self-improving demonstrations**: Matchmaker optimized 62.20/70.20 vs random demos 55.36/67.76 vs vanilla (no demos) 57.90/65.40 vs self-reflection 57.10/67.80 (MIMIC/Synthea acc@1) — [Matchmaker](https://arxiv.org/html/2410.24105v1)
- **Task decomposition + knowledge**: KcMF on Synthea GPT-3.5 zero-shot F1 9.1% → 42.4% full stack; removing pseudo-code raised over-matching errors from 13 to 60; Self-Indicator reasoning +7.73 F1 on MMM, +2.68 on Synthea; ensemble over knowledge sources 0.3125 → 0.4242 — [KcMF](https://arxiv.org/html/2410.12480v2)
- **Abbreviation rules + value-overlap cues in the prompt**: Prompt-Matcher Fabricated 70% → 79.3% (tip 4) → 90.9% (tips 1–3) → 91.8% (all) — [Prompt-Matcher](https://arxiv.org/html/2408.14507v3)
- **Fine-tuned SLM retriever + LLM reranker**: Magneto GDC MRR zero-shot 0.775 → fine-tuned+LLM-aug 0.866; LLM rerank over bipartite rerank at k=5 MRR 0.731→0.780, Recall@GT 0.375→0.475 — [Magneto](https://arxiv.org/html/2412.08194v2)
- **Knowledge injection on unseen schema**: Jellyfish CMS F1 7.14 → 59.29 — [Jellyfish](https://arxiv.org/html/2312.01678v5)
- **Model generation**: GPT-3.5→GPT-4 raised F1 for every prompt scope (Parciak); Matchmaker GPT-3.5 48.30 vs GPT-4 62.20 on MIMIC — [Parciak](https://arxiv.org/html/2407.11852v1); [Matchmaker Table 6](https://arxiv.org/html/2410.24105v1)
- **Table-tuning** moved DeepM schema matching 0.857 → 1.0 for ChatGPT — [Table-GPT](https://arxiv.org/pdf/2310.09263)
- **Combining LLM scopes + a string baseline**: 1-to-N + N-to-1 together found ~40 of 49 true matches with highest recall, with verification effort <10% of the search space (GPT-4 needed <10 candidate checks per dataset) — [Parciak](https://arxiv.org/html/2407.11852v1)

### Inferences
- The ordering of effect sizes is consistent: shortlist the right candidates (biggest), describe attributes richly (second), encode domain/abbreviation rules explicitly (large on weak models, smaller on strong ones), then demonstrations (small). This mirrors FORMA's pipeline order (profile/fingerprint → heuristics → agents).
- "One known mapping per table" is the cheapest big win and maps directly onto memory reuse of past approved mappings.
- Gains stack imperfectly: Matchmaker's refinement over ReMatch is +20 acc@1 but 0 or negative at acc@5, i.e. better ranking of the top, not more recall.

### Gaps
- No paper isolates the effect of few-shot examples drawn from *past mappings of the same customer* (memory reuse); Matchmaker's demos are synthetic, ReMatch's anchor is one pair per table.
- No controlled study of self-consistency / majority voting for schema matching was found; KcMF's IntGE votes over knowledge sources, not over samples.

---

## KQ2. How do the best systems produce confidence, and how well calibrated is it?

### Takeaway
Confidence is almost always *verbalised* (LLM asked for 0–100 or 0.00–1.00 per candidate, often with an explicit "no match" option) and is used for ranking and for deferral to humans; no schema-matching paper measured calibration (ECE/reliability), and the one adjacent calibration study (entity matching) finds the model over-confident, fixed partly by temperature scaling.

### Cited Findings
- Matchmaker: candidates posed as an MCQ with a "NONE of the above" option; prompt asks for a 0–100 relevance score per option, scored independently, "If there is no good match score No Match as 100", JSON output; prompt text says "Your scores will be calibrated" but no calibration method is described. The paper argues token-level probabilities are better calibrated than free-text but does not evaluate it. Observed failure: duplicate options (two `note_text` candidates) each scored 90. Deferral: entropy over the confidence vector picks which matches go to a human; entropy-based deferral beats random across 0–50% deferral rates (figure only) — [Matchmaker](https://arxiv.org/html/2410.24105v1)
- Magneto: reranking prompt asks the LLM for a 0.00–1.00 score per candidate with a one-shot example and fixed output format; the paper notes reranking quality is prompt-sensitive — [Magneto](https://arxiv.org/html/2412.08194v2)
- Prompt-Matcher: LLM returns an answer plus confidence Pr; Bayesian update P(a|v)=Pr·P(v)+(1−Pr)(1−P(v)) over the candidate result set; the worked example assumes 80% confidence; calibration and self-consistency listed as future work — [Prompt-Matcher](https://arxiv.org/html/2408.14507v3)
- Parciak et al. use "decisiveness" (share of yes/no vs unknown) as a reliability proxy: GPT-4 1-to-N 0.967, N-to-1 0.992, but N-to-M 0.029 (GPT-3.5: 0.123 / 0.218 / 0.021) — more context per prompt reduces decisiveness; run-to-run std dev mostly <0.1 — [Parciak](https://arxiv.org/html/2407.11852v1)
- KcMF uses majority voting across prompts (one per knowledge source) partly to filter badly formatted outputs — [KcMF](https://arxiv.org/html/2410.12480v2)
- Entity matching calibration (UncertaiNLP 2025, Kamsteeg et al.): fine-tuned RoBERTa matcher is over-confident; temperature scaling cuts ECE by up to 23.83% — [ACL Anthology 2025.uncertainlp-main.12](https://aclanthology.org/2025.uncertainlp-main.12)
- Human-in-the-loop EM (iConf 2025, Ito et al.) argues for inconsistency-driven sampling because uncertainty sampling misses "confidently wrong" predictions — [publicera.kb.se](https://publicera.kb.se/ir/article/view/47140)
- IBM ALFA (MASCOTS 2024): active learning for GNN schema alignment cuts human labelling cost 27–82% — [IBM Research](https://www.research.ibm.com/publications/alfa-active-learning-for-graph-neural-network-based-semantic-schema-alignment)

### Inferences
- Nobody in this literature has shown that a verbalised 0–100 score is calibrated for schema matching; it is a *ranking* signal that works for deferral ordering, which is a weaker claim than "87 means 87% likely right". A policy engine should treat such scores as ordinal and threshold them empirically on the customer's own review outcomes.
- Cheap robustness signals that *are* measured: decisiveness (did the model commit), agreement across prompt variants (KcMF), agreement between retrieval and reasoning candidate sources (Matchmaker), and embedding cosine to the chosen target (Matchmaker's remedial analysis uses ≥0.8).
- The explicit "no match / NONE" option is the one confidence mechanism every strong system adopts; absence of it produces over-matching (KcMF: 13→60 over-matching errors without rules).

### Gaps
- No ECE / reliability-diagram study for LLM schema matching exists in the sources found.
- Matchmaker's deferral curve values are in a figure only; exact accuracy-at-deferral-rate numbers not extractable.
- LSM's uncertainty-based human querying could not be read (PDF unparsable).

---

## KQ3. What is known about header-only vs header+values vs full table context?

### Takeaway
Header-only is consistently the worst serialisation when values are available; a name + inferred type + ~10 sampled values (frequency-weighted) is the common best default; richer *descriptions* help more than raw values when values are unavailable (privacy); but full-table / all-pairs context overloads the model and lowers both F1 and decisiveness.

### Cited Findings
- Magneto: columns serialised as name + inferred type + ~10 sample values; header-only zero-shot performs worst across datasets; "repeat" serialisation (name ×5) best zero-shot, verbose/default best after fine-tuning; priority sampling (frequency-weighted values) beats coordinated/weighted/frequency/random — [Magneto](https://arxiv.org/html/2412.08194v2)
- Magneto also warns name- and value-based signals both fail when representations differ, e.g. "FIGO grade 1" vs "G1" — [Magneto](https://arxiv.org/html/2412.08194v2)
- ReMatch and LSM are deliberately *data-free* (metadata only) for privacy; ReMatch shows descriptions, not just names, carry the signal (names-only −3 acc@1, −23 acc@5) and lists "enriching descriptions with source data where privacy allows" as future work — [ReMatch](https://arxiv.org/html/2403.01567v2); [LSM](https://www.microsoft.com/en-us/research/?p=909858)
- Parciak et al.: no instances at all (names + descriptions); more pairs per prompt (N-to-M) → lower F1 (0.469 vs 0.580 for N-to-1 on GPT-4) and near-zero decisiveness — [Parciak](https://arxiv.org/html/2407.11852v1)
- SemStruct: header-only text serialisation "already effective" *when a GNN adds value context*; on SOTAB-SM (opaque headers) PLM-only models struggle and row-level value co-occurrence is what rescues matching; zero-initialised row nodes performed as well as semantic ones (topology matters) — [SemStruct](https://www.alphaxiv.org/abs/2605.30729)
- ConStruM: adds a budgeted "context pack" of the most discriminative evidence, including groups of near-identical columns on both sides summarised as differentiation cues; add-on to any matcher's final LLM prompt — [ConStruM](https://arxiv.org/abs/2601.20482)
- Industrial ERP case (DaWaK 2026, ColRel): ERP column names are often coded/abbreviated and metadata "sparse or weakly informative"; business dictionaries used to decode names and generate short NL descriptions per column (one LLM call per column, not per pair) — [arXiv 2608.26750](https://arxiv.org/abs/2608.26750)
- Prompt-Matcher's Abbreviation-Match prompt (domain context + abbreviation rules + value-overlap cues) lifts Fabricated-dataset accuracy 70→91.8 — [Prompt-Matcher](https://arxiv.org/html/2408.14507v3)
- SpreadsheetLLM: adding cell *format* information (colour, borders) hurt performance and was dropped — [SpreadsheetLLM](https://arxiv.org/html/2407.09025v2)

### Inferences
- The practical recipe is: per-column card = header + decoded/expanded name + inferred type + ~10 frequency-weighted distinct values + a one-line LLM-generated description; present a *shortlist* of target candidates (not the whole target), and include cues that distinguish near-duplicate targets.
- For codes-vs-names (e.g. unit "EA" vs "PCS", Thai/English headers) the literature's answer is explicit dictionaries/synonym tables injected into the prompt, not hoping the model infers them — Jellyfish's "specific knowledge" slot and KcMF's EaK are the formal versions.
- Full-sheet dumps are the wrong unit: both Parciak (N-to-M) and SpreadsheetLLM (vanilla encoding) show degradation; compress/shortlist first.

### Gaps
- No LLM-prompting ablation that varies *number of sample values* (0/5/10/50) for schema matching was found; Magneto varies sampling *strategy*, not count.
- No study on bilingual (Thai/English) or non-Latin headers; closest is "coded/abbreviated ERP labels" (ColRel) and Valentine's fabricated noise.

---

## KQ4. What does research say about iterative refinement / self-correction for table tasks?

### Takeaway
Structured iteration (plan → operate on the table → re-inspect) helps table reasoning (Chain-of-Table +5.8 pts WikiTQ, +10.3 on large tables), and agent loops that call matching tools and re-check results can catch mapping errors; but *unstructured* self-reflection did not help schema matching (Matchmaker: 57.10 vs 62.20), and bootstrapping from an external evaluator/verifier is what worked.

### Cited Findings
- Matchmaker ablation: self-reflection variant 57.10 (MIMIC) / 67.80 (Synthea) acc@1 vs optimized 62.20 / 70.20 and vanilla 57.90 / 65.40 — self-reflection ≈ no gain on MIMIC. The working mechanism is instead an LLM *evaluator* (0–5 rating, "be lenient") scoring traces on unlabeled queries, with the best traces bootstrapped as ≤4 demonstrations — [Matchmaker](https://arxiv.org/html/2410.24105v1)
- Matchmaker "remedial action" (propagating corrections across directly-mapped attributes with cosine ≥0.8) yields "relatively modest" gains vs human deferral, and relies on direct mappings existing — [Matchmaker Appendix D.4](https://arxiv.org/html/2410.24105v1)
- Chain-of-Table (ICLR 2024, Google): LLM iteratively generates table operations and updates the table as a reasoning chain; PaLM-2 TabFact 86.61% (+1.98 over next best), WikiTQ 67.31% (+5.83 over Dater), large tables 44.87% (+10.25) — [Chain-of-Table, arXiv 2401.04398](https://arxiv.org/abs/2401.04398)
- Interactive Data Harmonization with LLM Agents / Harmonia (NYU, NOVAS'25): agent calls schema-matching tools (map table→target schema, top-k column matches, value matching), inspects returned matches and corrects a wrongly mapped column in a follow-up step; proposes agentic harmonization with a UI and harmonization-primitive library — [arXiv 2502.07132](https://arxiv.org/abs/2502.07132)
- Prompt-Matcher is itself an iterative loop: select correspondences → GPT-4 verifies → Bayesian update, in k budgeted rounds — [Prompt-Matcher](https://arxiv.org/html/2408.14507v3)
- AutoDCWorkflow (UIUC, 2024): LLM plans data-cleaning as Select Target Columns → Inspect Column Quality → request OpenRefine operations, iterating; benchmark 142 purposes / 96 tables; Gemma-2-27B gives highest-quality tables, Gemma-2-9B produces workflows closest to human ones; compares against CleanAgent (Qi & Wang 2024) — [AutoDCWorkflow, arXiv 2412.06724](https://arxiv.org/html/2412.06724v3)
- Cocoon (Zhang, Huang, Wu; 2024): LLM generates semantic cleaning rules, combined with statistical error detection, decomposed into a human-like workflow because LLMs "cannot handle full data cleaning in one shot"; claims to beat SOTA cleaning systems on standard benchmarks (numbers not on abstract page) — [arXiv 2410.15547](https://arxiv.org/abs/2410.15547)
- SheetCopilot (NeurIPS 2023): single-generation task completion 44.3% on 221 tasks / 28 workbooks; SheetAgent (2024) reports 61.1% Pass@1 with GPT-3.5 on the same benchmark via planner + informer + retriever loop (aggregator marks both unverified) — [NeurIPS poster](https://neurips.cc/virtual/2023/poster/70193); [SheetAgent, arXiv 2403.03636](https://arxiv.org/pdf/2403.03636)
- SpreadsheetBench (NeurIPS 2024): 912 real forum instructions / 2,729 test cases; all LLMs and spreadsheet agents score 0.05%–23.65% under the OJ-style metric; authors stress a single test spreadsheet produces false-positive, over-fitted solutions, hence ~3 test cases per instruction — [SpreadsheetBench, arXiv 2406.14991](https://www.arxiv.org/pdf/2406.14991)
- Data-wrangling agent benchmarks: InfiAgent-DABench (ICML 2024) 603 questions / 124 CSVs, closed-form via format prompting, DAAgent +3.9% over GPT-3.5 — [ICML 2024](https://proceedings.mlr.press/v235/hu24s.html); DS-1000 (2022) best Codex-002 pass@1 43.3%, reference solutions avg 3.6 lines — [DS-1000](https://arxiv.org/pdf/2211.11501); Spider 2.0 (2024/25) 632 enterprise text-to-SQL workflows, o1-preview agent ~17% (later revision 21.36%), GPT-4o 10.1%, vs ~91% on Spider 1.0 — [spider2-sql.github.io](https://spider2-sql.github.io/); [alphaXiv 2411.07763v2](https://www.alphaxiv.org/abs/2411.07763v2)
- Programming by example: "Is Programming by Example solved by LLMs?" (2024) finds pretrained models weak at PBE but fine-tuning helps much when test problems are in-distribution — [arXiv 2406.08316](https://arxiv.org/pdf/2406.08316.pdf); a 2025 VLDB QDB workshop paper evaluates LLMs generating input-table→output-table transformation functions, noting classical PBE (FlashFill/PROSE) is restricted to narrow DSLs — [QDB25_6](https://www.vldb.org/2025/Workshops/VLDB-Workshops-2025/QDB/QDB25_6.pdf); Gulwani (2023) proposes extending the Flash Fill DSL with LLM operators — [SEMLA'23 slides](https://semla.polymtl.ca/wp-content/uploads/2023/08/semla23-SumitGulwani.pdf)

### Inferences
- What works is "iterate with an external check" — a tool call, a test case, an evaluator model, a Bayesian update — not "ask the model to reconsider". For schema mapping that means re-running the mapping on sample rows and checking value-level constraints (type, enum, uniqueness) as the correction signal.
- Benchmarks with real files (SpreadsheetBench ≤24%, Spider 2.0 ≤21%) are an order of magnitude harder than clean academic tables (WikiTQ 67%, TabFact 87%); any agent in the data path needs policy gating and human review, which matches FORMA's non-negotiable rules 2 and 6.

### Gaps
- Cocoon and CleanAgent result tables not read (abstract pages only).
- DA-Code and Text2Analysis: no numbers found in searches.
- No paper quantifies self-consistency (sampling k times and voting) for schema matching.

---

## KQ5. How is spreadsheet structure (headers, sections, totals, multi-sheet) detected reliably?

### Takeaway
Table-region detection is solved to ~76–79% F1 at exact-boundary (EoB-0) with fine-tuned LLMs over compressed encodings (vs 66.6% for the TableSense CNN); header recognition is ~0.80–0.85 F1 for very large models but collapses on hierarchical/multi-row headers and merged cells, which every paper names as the hardest residual case; sections/totals/multi-sheet handling exists only as prompt heuristics and routing rules, not as measured sub-tasks.

### Cited Findings
- TableSense (Microsoft Research, AAAI 2019): treats the cell matrix like pixels; cell featurisation + CNN with boundary refinement + active-learning sampling; dataset 22,176 tables on 10,220 sheets; recall 91.3% / precision 86.5% at EoB-2; at strict EoB-0 80.8% recall / 78.1% precision vs Mask R-CNN 48.4/40.2 (secondary summary) — [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/3770); [arXiv 2106.13500](https://arxiv.org/abs/2106.13500)
- SpreadsheetLLM / SheetCompressor (Microsoft, 2024): (1) structural-anchor extraction keeps heterogeneous rows/cols near table boundaries, drops homogeneous ones >k=4 cells away — removes ~75% of content while preserving 97% of boundary rows/cols; (2) inverted-index translation (value → address ranges, lossless); (3) data-format-aware aggregation by number-format string / rule-based type. Compression 24.79× (1,548,577 → 62,469 tokens). Table detection F1 (EoB-0, 188 sheets / 311 tables test set): GPT-4 ICL vanilla 0.154 → compressed 0.410; fine-tuned GPT-4 vanilla 0.520 → compressed 0.759 → compressed without aggregation 0.789; TableSense-CNN 0.666; open-source fine-tuned 0.660–0.726. Spreadsheet QA: Chain of Spreadsheet 0.684, fine-tuned 0.743 vs Binder 0.622 / GPT-4 0.466. Limits: no cell colour/borders encoded (too many tokens, and adding format hurt); no semantic compression of NL cells — [SpreadsheetLLM, arXiv 2407.09025](https://arxiv.org/html/2407.09025v2)
- Table header recognition with LLMs (Okhotin & Dorodnykh, VLDB 2025 TaDA + ISP RAS journal): 237 tables, 8 models × 6 prompt strategies, zero/few-shot; 405B models F1 ≈ 0.80–0.85, 7B models 0.06–0.30; step-by-step instructions + search criteria + examples help large models but *degrade* small ones (context overload); largest errors on hierarchical headers and merged cells even for large models; test split simple/medium/complex, complex = header cells below row 3 and spanning columns — [TaDA25_14](https://tabular-data-analysis.github.io/tada2025/papers/TaDA25_14.pdf); [ISP RAS](https://ispranproceedings.elpub.ru/jour/article/view/2049/1871)
- SpreadsheetAgent (ACL 2026): a structure-extraction stage that explicitly looks for hierarchical headers, merged cells and multi-sheet layouts before reasoning — [ACL 2026 long 86](https://aclanthology.org/2026.acl-long.86.pdf)
- Multi-agent multi-format spreadsheet reasoning (2026): prompt asks the LLM to detect tables and build hierarchical trees for top-row and left-column headers; treats column-only, row-only and two-way header layouts as separate cases — [arXiv 2604.12282](https://arxiv.org/pdf/2604.12282)
- SQuARE (2025): scores sheet complexity from header depth and merge count; routes simple sheets to semantic retrieval and complex ones to a path that preserves header hierarchy and uses SQL for exact numeric answers — [arXiv 2512.04292](https://arxiv.org/pdf/2512.04292)
- Hierarchical-table benchmarks: CHiTab (100 complex tables from PubTables-1M, tests recovery of parent–child spanning-header links) — [arXiv 2511.08298](https://arxiv.org/html/2511.08298v1); RealHiTBench separates hierarchical column headers from row headers and builds header trees — [summary](https://www.themoonlight.io/review/realhitbench-a-comprehensive-realistic-hierarchical-table-benchmark-for-evaluating-llm-based-table-analysis)
- SpreadsheetBench real-world files drive all agents to ≤23.65% — [SpreadsheetBench](https://www.arxiv.org/pdf/2406.14991)

### Inferences
- The reliable part is *where the table is* (anchor-row heterogeneity + fine-tuned detector). The unreliable part is *what the header means* when it spans rows/columns — exactly the Thai ERP export case (title rows, two-row headers, group/subtotal rows). Deterministic pre-structuring (row-type classification: title / header / data / subtotal / blank) followed by an LLM only on the ambiguous header block is the pattern the 2025–26 agents converge on (SQuARE's routing, SpreadsheetAgent's extraction stage).
- Format signals (bold, fills, borders) are what humans use for section/total rows, yet SpreadsheetLLM found them token-expensive and harmful as raw input — compress them into a few per-row flags rather than per-cell attributes.
- No one has measured "total/subtotal row detection" or "multi-sheet relationship detection" as a benchmarked sub-task; treat these as in-house heuristics with your own ground truth (FORMA's synthetic corpus is the right place).

### Gaps
- TaDA25 PDF unparsable here; exact per-complexity accuracy and the size of the hierarchical-header accuracy loss not extracted.
- No benchmark numbers found for section/group rows, subtotal rows, or multi-sheet linkage detection.
- Microsoft's "semantic structure in spreadsheets" line (header/aggregation role classification, e.g. earlier Dong et al. work) was not retrieved beyond TableSense and SpreadsheetLLM.

---

## KQ6. Open problems the papers themselves list, and the benchmark-vs-messy-reality gap

### Takeaway
Authors agree on the same open list: retrieval misses are unrecoverable, prompts are brittle, LLM cost/latency is high, no-match and m:n cases are poorly handled, domain/abbreviation knowledge must be supplied by humans, confidence is uncalibrated, and real enterprise files (coded ERP labels, multi-table alignment) remain far harder than the public healthcare benchmarks — so every paper ends by recommending a human in the loop.

### Cited Findings
- Magneto: if the true match is not in top-k the reranker cannot recover it; prompt sensitivity; LLM cost/latency; weak coverage of under-represented domains without external knowledge; Valentine datasets are mostly lexical so fine-tuning helps less; Unicorn results may reflect leakage; no optimal LLM/SLM identified — [Magneto](https://arxiv.org/html/2412.08194v2)
- Matchmaker: not full automation, best used with a human; overreliance and downstream error propagation; variance from LLM temperature; remedial correction needs direct mappings that may not exist — [Matchmaker](https://arxiv.org/html/2410.24105v1)
- ReMatch: only 1:1 and m:1 evaluated (acc@K undefined for m:n); single-expert ground truth; retrieval benefit depends on target size; future work: type constraints, PK/FK, better guidance, enriching descriptions with data where privacy allows — [ReMatch](https://arxiv.org/html/2403.01567v2)
- Parciak et al.: possible training-data overlap with public schemas; baseline thresholds tuned on ground truth (unrealistic); only 1:1; LLM over-weights name similarity (e.g. matching two unrelated "time" attributes); future: explanations, NL feedback tool, proprietary schemas, AdnEV-style ensembling — [Parciak](https://arxiv.org/html/2407.11852v1)
- KcMF: choosing domain knowledge bases still needs humans; pairwise enumeration is costly; efficiency left to future work — [KcMF](https://arxiv.org/html/2410.12480v2)
- Prompt-Matcher: calibration and self-consistency are future work; results track GPT-4 accuracy (56.7% on one hard task); text/table inconsistency (73% vs 70.0%) — [Prompt-Matcher](https://arxiv.org/html/2408.14507v3)
- Industrial ERP case (ColRel, DaWaK 2026): metadata alone insufficient; coded/abbreviated labels need business dictionaries; advantage is strongest only in weak-signal, semantically related cases — [arXiv 2608.26750](https://arxiv.org/abs/2608.26750)
- LSM (Microsoft): linguistic matchers showed low accuracy on real Microsoft-customer schemata, motivating a learned, data-free matcher — [Microsoft Research](https://www.microsoft.com/en-us/research/?p=909858)
- LLMatch: motivates SchemaNet because public benchmarks do not capture multi-table enterprise alignment — [LLMatch](https://arxiv.org/abs/2507.10897)
- SpreadsheetBench: real forum problems → 0.05–23.65%; "stark difference" to human expertise — [SpreadsheetBench](https://www.arxiv.org/pdf/2406.14991)
- Spider 2.0: enterprise workflows ~17–21% vs ~91% on Spider 1.0 — [spider2-sql.github.io](https://spider2-sql.github.io/)
- Jellyfish: fine-tuned model fails on unseen CMS (7.14 F1) until knowledge injected — [Jellyfish](https://arxiv.org/html/2312.01678v5)
- SpreadsheetLLM: formatting not encoded; NL cells not compressed — [SpreadsheetLLM](https://arxiv.org/html/2407.09025v2)
- Header recognition: hierarchical headers + merged cells remain the largest error source even for 405B models — [TaDA25_14](https://tabular-data-analysis.github.io/tada2025/papers/TaDA25_14.pdf)
- Interactive harmonization (Harmonia): frames the research agenda as agentic + interactive UI + reusable pipelines, listing open challenges rather than solving them — [arXiv 2502.07132](https://arxiv.org/abs/2502.07132)

### Inferences
- Nothing in this literature addresses Thai/English bilingual headers or Thai Buddhist-era dates; the closest evidence (coded ERP labels, Valentine noise, "FIGO grade 1"/"G1") says the fix is explicit, versioned synonym/dictionary knowledge injected into the prompt and treated as data — consistent with FORMA keeping synonyms in the schema file and Thai rules as explicit ops.
- The benchmark numbers (62–70% acc@1, 0.87 MRR) are *upper bounds* for a new customer's first file; the literature's own prescription — retrieval shortlist, rich column cards, explicit no-match, entropy-ranked review queue, one anchor mapping from memory — is the recipe that gets from there to an auto-approve regime, with policy thresholds learned from review outcomes rather than from the model's self-reported confidence.

### Gaps
- No paper evaluates cross-lingual or mixed-script schema matching; none evaluates recurring (every-14-days) files from the same source, i.e. drift detection against an approved mapping — FORMA's core loop has no academic benchmark.
- Full texts of Schemora, LLMatch, ConStruM, SemStruct, Cocoon, CleanAgent, Unicorn and LSM were not read; their result tables may contain relevant numbers not captured here.
