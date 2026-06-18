# data-science Role Memory

_Auto-injected into Claude Code sessions inside `data-science/`. Last bootstrapped: 2026-06-01. Last reconciled: 2026-06-18._

## Mission

Build, validate, and productionize churn, fraud, and propensity models for Acme clients. All work targets explainability, regulatory defensibility, and on-prem deployability — sensitive raw data never leaves the client's environment. We receive only pseudonymized IDs (hashed email/phone, device IDs); enrichment features are fetched on our side against those IDs.

---

## Projects in this role

**Rules / knowledge base**
- `model-skills` — Cross-project rules catalog for churn / fraud / propensity modeling best practices (30+ rules, GATE/HEURISTIC classification, phase-prefixed, 8-phase lifecycle); the editable rules source of truth

**Churn / propensity models**
- `churn-model` — CatBoost PD scorer for ClientA SaaS churn prediction using enrichment features only; joblib wrapper + CLI shipped; AUC ~0.72 on holdout
- `fraud-experiments` — Improve generalization of the fraud detection model for ClientB; baseline + 4 experiments: RFE, autoencoder, WGAN-GP upsampling, VAE+GAN

**Feature engineering**
- `feature-catalog` — Partner × variable inventory: features across multiple data partners with canonical names, categories, derivation logic, and per-partner runnable transforms; backing `master.csv`

---

## Best practices

- **Consult `feature-catalog/` before building any model from partner data.** It is the canonical inventory of every partner feature available — read it first instead of inventing feature names or guessing what a partner provides.
  - `feature-catalog/master.csv` — source of truth. Schema: `id, Partner, Source Variable, Category, Canonical Name, Description, Derivation Logic`. Filter by `Partner` to see what a given feed offers; use `Canonical Name` in code.
  - `feature-catalog/by_category/<category>.csv` — same rows split into categories (demographic, financial, transactional, device, telecom, etc.) for picking features by signal type.
  - `feature-catalog/by_partner/<partner>_transform.py` — runnable ETL reproducing a partner's features from the raw extract; copy/adapt rather than writing partner parsing from scratch.
  - **Never edit `master.csv` blind or auto-regenerate it** — it is hand-curated.
- **Seed everything with 42** — `random_state=42` and `random_seed=42` hardcoded across all training scripts; Optuna studies also seeded. Reproducibility is mandatory for audit.
- **Parquet over CSV for large datasets** — all intermediate splits stored as `.parquet` (pyarrow); CSVs only for deliverables or cross-team handoffs.
- **Joblib for production artifacts** — every shipped model is a `.joblib` wrapping a custom class (not bare sklearn Pipeline). The wrapper holds: preprocessing pipeline + estimator + feature schema + version + thresholds.
- **Model card per shipped model** — `MODEL_CARD.md` documents training data, feature audit, hyperparameters, decile table, drift SOP, and retraining steps. Non-negotiable for client handoff.
- **Numbered pipeline scripts** — stages prefixed `01_`…`09_` for execution-order clarity; phases prefixed `phase1_`…`phase7_` within a project.
- **tqdm on every loop** — no silent waits. Progress written to `/tmp/<script>_progress.txt` for long-running phase scripts.
- **Optuna for hyperparameter tuning** — always save `optuna_best.json`; log to WARNING level to suppress trial spam. TPE sampler preferred.
- **PSI as drift monitor** — prediction PSI vs train baseline monthly; 0.10 = watch, 0.25 = retrain. Per-feature PSI tracked separately.
- **On-prem deploy awareness** — clients receive only pseudonymized IDs (hashed email/phone, device IDs). Raw sensitive data never moves.
- **Leakage-free splits** — validation set for early stopping only, test set for final evaluation only. VFL stacking uses `StratifiedKFold` on train only.
- **SQLite manifest + live dashboard** — multi-run projects log every CV run to `output/experiments.db`; `dashboard.py` serves at `localhost:8770`.
- **One feature SHAP-dominating 3–5× the next = leakage red flag** — investigate target leakage or pipeline contamination before shipping.
- **PSI-first feature filter (pre-tuning)** — per-feature PSI threshold 0.10 applied as hard gate BEFORE SHAP ranking, correlation pruning, or Optuna. Dropping PSI-unstable features is often the primary lever to fix a drifting score distribution.
- **Optuna objective with explicit overfit penalty** — `val_auc − K · max(0, gap − threshold)`. Plain AUC maximization produces overfit models that fail PSI gates.
- **IV > 0.5 is a leakage alarm, not a win** — treat implausibly strong single-feature IV as guilty until you can document the mechanism; same for AUC > 0.90. Do not celebrate; re-audit first.
- **SHAP validates; it does not launder black boxes** — post-hoc SHAP on an opaque model family does not satisfy explainability requirements. SHAP must be applied to an allowed family (LightGBM / XGBoost / LR / CatBoost).

---

## Patterns

### Phase-based pipeline scripts

```
phase1_matching.py   → ID overlap, chunk-based parquet reads, gc.collect()
phase2_concordance.py
phase3_features.py   → feature engineering
phase4_assembly.py / phase4_training.py
phase5_validation.py
phase6_*.py          → scoring, threshold tuning, final output
phase7_*.py          → imputation / propensity
```

Each phase writes intermediate parquet files; next phase reads them. Makes resumption trivial after crashes.

### Numbered stage scripts

```
01_extract → 02_filter_split → 03_audit → 04_build_features →
05_bakeoff → 06_grid → 07_v2 → 08_optuna_tune → 09_train_final_and_package
```

Stage 09 is the canonical reproducibility entrypoint.

### 6-stage automated orchestrator

```
Stage 1: Baselines (multiple frameworks, default params)
Stage 2: Tune top framework (Optuna, 30 trials)
Stage 3: Feature selection (importance-rank + composite score across various budgets)
Stage 4: Re-tune on selected features
Stage 5: Ensemble (equal-weight avg of top distinct models)
Stage 6: Final holdout eval + PSI (feature-level + score-level)
```

Composite score = `cv_auc_mean − 0.5·overfit_gap − 0.5·cv_auc_std` — penalizes overfit AND variance.

### Sentinel value handling

Multiple data partners use non-null sentinels for "not found" or "feature missing." Common values:
- `-1` → user not found in partner extract
- `-10` → feature unavailable
- `10000` → missing (for days-since-* features)
- `-9999` → partner feature unavailable

All must be converted to `NaN` as the **first** preprocessing step before any feature engineering.

### CatBoost for tabular classification

- Depth 6, lr 0.012–0.017, l2 = 4–5, balanced class weights, ~700 iterations
- Frequency-encode low-cardinality categoricals before fitting
- Presence flags for skewed count features (e.g., `days_inactive_count → was_ever_inactive`)
- Anchor age-derived columns to a `REF_YEAR` constant; retrain when stale > 12 months

### XGBoost anti-overfit config (reusable across projects)

```python
max_depth=3, min_child_weight=100, gamma=2.0,
subsample=0.6, colsample_bytree=0.4, colsample_bylevel=0.6,
reg_alpha=3.0, reg_lambda=10.0, max_delta_step=1
```

Save this as `results/baseline/regularization_config.json` and reload in all subsequent experiments.

### WGAN-GP for synthetic / minority-class upsampling

- Separate generators per class (positive + negative) to preserve class-conditional distributions
- Post-processing: clamp → zero-rate correction → quantile mapping → round/snap → target correlation check
- **Clamp-first ordering is critical**: ratio features can produce deeply negative values; clamping first converts them to 0, enabling zero-rate correction to see true sparsity
- ~500 epochs, batch 256, Adam lr=1e-4, betas=(0.5, 0.9), gradient penalty lambda=10

### WoE-first feature preparation

- ALL categorical features → mandatory WoE encoding via OptBinning before any feature selection
- High-cardinality (> 20 unique values) → OptBinning categorical with `output_woe: true`
- Low-cardinality (≤ 20 unique) → direct WoE encoding
- Standard scaling skipped for WoE features (already log-odds scale)

### Correlation pruning + feature cap

Post-PSI step before Optuna: drop Pearson > 0.90 (keep higher SHAP rank), cap features at 50 (`CORR_THRESHOLD=0.90`, `FEATURE_CAP=50`).

---

## Anti-patterns

- **Do not skip the convergence check** — minimum 3 experiments required before selecting a model; skipping leads to premature selection on lucky runs.
- **Do not use bare sklearn Pipeline as the artifact** — thresholds, feature schema, and version must travel with the model for audits; always use a custom wrapper class.
- **Do not auto-detect features for SHAP** — always pass `--features` explicitly; auto-detection fails on string-column datasets.
- **Do not tune on the validation set** — Optuna uses an inner train/val split; test set is for final evaluation only. Peeking inflates AUC by several points.
- **Do not ignore sentinel values** — treating -1/-10/10000/-9999 as real numeric values silently corrupts feature distributions.
- **Do not mock DB tests** — test against real joblib artifacts and real data splits; mocked preprocessing masks schema drift bugs.
- **Model-score PSI can fail even when all feature PSIs pass** — stable features can combine into a drifting score; always compute PSI on the score distribution separately.
- **Calibration is a separate axis from AUC/KS** — when the score is used as a probability (pricing, expected-loss), fit a reliability curve and recalibrate. A well-ranking, miscalibrated model misprices systematically.

---

## Gotchas

- `[churn-model]` — Engagement features (`social_network_score`, `active_days_last_30`) have 4× lower fill on fresh batches from some clients; predictions skew to middle deciles when this collapses. Monitor fill rate before every scoring run.
- `[churn-model]` — `REF_YEAR` baked into age-derived feature computation; retrain when reference year is > 12 months stale.
- `[fraud-experiments]` — Val (OOT newer onboarding) and train/test are different acquisition channels, not just different time periods. PR-AUC drop from test to val is population/channel drift, not time drift. Do not conflate the two.
- `[fraud-experiments]` — GAN upsampling dramatically improved calibration (ECE) without improving rank ordering. Use calibration ECE as a separate reporting metric alongside AUC.
- `[feature-catalog]` — `--universe` is **required** on partner transforms (CLI arg, errors if omitted). Without it, absent users are silently dropped instead of flagged with a coverage indicator. This was a real bug; always pass the full canonical seed as the universe.
- `[feature-catalog]` — Partner schema drift: some client extracts rename columns (e.g., `*_v2` suffix variants). Map to catalog canonical names before the transform runs; emit absent fields as null; drop all-null columns.
- `[feature-catalog]` — pandas with pyarrow-backed dtypes: `.value_counts(normalize=True).values ** 2` and `sum()` over boolean Series break; use `.to_numpy(dtype=float)` / explicit int casts.
- `[model-skills]` — CatBoost is incompatible with Python 3.14+; pre-flight check auto-excludes it. Always run Python version check before proposing frameworks.
- `[model-skills]` — Always pass `--data-tag` to all CLI commands; column name varies across datasets. Never rely on a hardcoded default.
- `[model-skills]` — OptBinning is required from v0.3.0+. Install with `uv sync` or `pip install -e .`.

---

## Tools / stacks

**ML frameworks**
- CatBoost — primary for tabular churn/propensity (handles categoricals natively, balanced class weights)
- XGBoost — fraud experiments (strong regularization, early stopping)
- LightGBM — often Stage 1 baseline winner in bakeoffs
- scikit-learn — preprocessing, LogisticRegression meta-learner, RFE, StratifiedKFold
- Optuna — hyperparameter tuning (TPE sampler, 30–60 trials, save `optuna_best.json`)
- SHAP — explainability for audit defensibility
- OptBinning — WoE binning, IV computation (required in model-skills workflow)

**Deep learning / generative**
- PyTorch — WGAN-GP (synthetic data, fraud upsampling)
- sklearn MLPRegressor — autoencoder (fraud experiments; bottleneck 256 → 32 → 256)

**Data engineering**
- pandas, numpy — universal
- pyarrow / parquet — intermediate storage for large datasets
- tqdm — mandatory progress bars on all loops
- joblib — model serialization (`.joblib` artifacts)
- SQLite — experiment manifest

**Infrastructure / CLI**
- Click 8.1+ — CLI wrappers in model-skills
- uv — package manager for model-skills (fallback: venv + pip)
- Polars — used in model-skills engines alongside pandas

---

## Vocabulary / glossary

| Term | Definition |
|------|-----------|
| **PD** | Probability of Default — or, by analogy, probability of churn; binary classification target |
| **NTC** | New-to-Credit (or new-to-platform) — users with no prior signal in a partner's dataset |
| **DPD** | Days Past Due — days a payment is overdue; basis for default labeling in credit tasks |
| **AUC** | Area Under the ROC Curve — primary discrimination metric |
| **KS** | Kolmogorov-Smirnov statistic — max separation between good/bad CDFs; > 0.20 acceptable |
| **Gini** | 2×AUC − 1; 0.30–0.60 typical for churn/credit risk |
| **IV** | Information Value — feature predictive power; < 0.02 weak, > 0.5 suspicious (leakage) |
| **PSI** | Population Stability Index — distribution drift; < 0.10 stable, > 0.25 retrain |
| **WoE** | Weight of Evidence — log-odds transformation of feature bins relative to target |
| **OOT** | Out-of-Time — holdout cohort from a later time period for temporal validation |
| **overfit gap** | `AUC_train − AUC_test`; > 0.03 flagged; > 0.05 rejection criterion |
| **decile lift** | Top decile bad rate vs base rate; 1.75× = top 10% has 1.75× the average churn rate |
| **pseudonymized ID** | Hashed email / phone / device ID — no raw PII shared between us and the client |
| **composite score** | `cv_auc_mean − 0.5·overfit_gap − 0.5·cv_auc_std` — penalizes overfit AND variance in model selection |
| **ECE** | Expected Calibration Error — measures probability calibration quality |
| **WGAN-GP** | Wasserstein GAN with Gradient Penalty — stable GAN variant for tabular data |
| **feature catalog** | `feature-catalog/master.csv` — the canonical inventory of all partner features available for modeling |
| **coverage flag** | A binary derived feature indicating whether a user was found in a given partner's extract |
| **universe** | The full canonical seed of all user IDs that should be scored; absent users must be flagged, not silently dropped |

---

## Open questions

- **churn-model** — AUC ceiling is ~0.72 with single-partner enrichment features; multi-partner join is the clear path to beat it. Cross-partner join for a full-featured model not yet built.
- **churn-model** — Engagement feature fill collapse on some client batches is unexplained; root cause unknown. May reflect partner data pipeline changes.
- **fraud-experiments** — All 4 experiments reduced overfit gap but none hit the precision/recall target on OOT validation. Fundamental population/channel drift between train and val is the bottleneck, not model architecture.
- **model-skills** — WoE-first workflow not yet validated end-to-end; next full run needed.
- **feature-catalog** — Partner schema drift handling for v2-renamed columns should be automated via a mapping config file rather than done manually per client extract.

---

## Solved problems index

| If you need to... | Look at | Why |
|---|---|---|
| Build a multi-phase resumable pipeline over large parquet data | `data-science/churn-model/phase1_*.py` → `phase7_*.py` | Phase scripts, each reading/writing intermediate parquet; gc.collect() pattern; crash-safe resumption |
| Package a model for on-prem client delivery (joblib + schema + thresholds) | `data-science/churn-model/package/scorer.py` | Custom wrapper class holding preprocessing + estimator + feature schema + version + thresholds; the canonical joblib pattern |
| Run Optuna with seeded reproducibility and overfit penalty | `data-science/churn-model/scripts/08_optuna_tune.py` | TPE sampler, seed 42, `val_auc − K·gap` objective, saves `optuna_best.json` |
| Log every experiment run to SQLite and serve a live progress dashboard | `data-science/churn-model/src/manifest.py` + `dashboard.py` | SQLite manifest pattern; dashboard at localhost:8770; copy these two files into any multi-run project |
| Apply WoE transformation via OptBinning as a mandatory preprocessing gate | `data-science/model-skills/src/engines/transformation_engine.py` | WoE-first pipeline; high-cardinality vs low-cardinality split; skip standard scaling for WoE features |
| Implement SHAP explainability for audit defensibility | `data-science/model-skills/src/engines/shap_engine.py` | SHAP beeswarm + waterfall; `--features` must be passed explicitly |
| Implement WGAN-GP for synthetic tabular data or minority-class upsampling | `data-science/fraud-experiments/src/experiment3a_gan_upsample.py` | Separate class-conditional generators; 500 epochs; clamp-first postprocessing ordering |
| Look up authoritative metric thresholds (IV/PSI/AUC/KS/Gini bands) | `data-science/model-skills/docs/metrics-reference.md` | Canonical reading conventions + quick gate lookup table |
| Handle sentinel values before feature engineering | `data-science/fraud-experiments/src/preprocess.py` | Four sentinel values → NaN as first preprocessing step |
| Add a derived coverage flag to a partner transform | `data-science/feature-catalog/by_partner/vendor1_transform.py` | `DERIVED` dict + `FEATURE_NAMES` + `--universe` required; `build_catalog.py` auto-validates |

---

## How to contribute

- Write inline `[LESSON]: <text>` / `[PATTERN]: <text>` / `[GOTCHA]: <text>` / `[TOOL]: <text>` in any `memory/PROJECT.md` or commit message — the Stop hook scrapes these automatically.
- Deeper insights are auto-reconciled into this file by `/role-promote data-science` (fired automatically when the brain is > 7 days stale with pending activity).
