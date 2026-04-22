Three runs side by side

┌──────────────────────┬───────────────────────────────┬─────────────────────────────────┬──────────────────────────────┐
│ Model │ REAL only (n_test=56, anom=4) │ SYNTH only (n_test=55, anom=25) │ MERGED (n_test=111, anom=29) │
├──────────────────────┼───────────────────────────────┼─────────────────────────────────┼──────────────────────────────┤
│ isolation_forest │ F1=0.00 ROC=0.58 │ F1=0.44 ROC=0.96 │ F1=0.00 ROC=0.48 │
├──────────────────────┼───────────────────────────────┼─────────────────────────────────┼──────────────────────────────┤
│ local_outlier_factor │ F1=0.00 ROC=0.46 │ F1=0.51 ROC=0.97 │ F1=0.00 ROC=0.43 │
├──────────────────────┼───────────────────────────────┼─────────────────────────────────┼──────────────────────────────┤
│ one_class_svm │ F1=0.10 ROC=0.49 │ F1=0.69 ROC=0.96 │ F1=0.32 ROC=0.45 │
├──────────────────────┼───────────────────────────────┼─────────────────────────────────┼──────────────────────────────┤
│ elliptic_envelope │ F1=0.00 ROC=0.51 │ F1=0.00 ROC=0.94 │ F1=0.00 ROC=0.44 │
└──────────────────────┴───────────────────────────────┴─────────────────────────────────┴──────────────────────────────┘

Three completely different regimes, and together they tell a coherent (and damning) story.

What I think

1. Synthetic-only looks great — and that's the red flag

ROC-AUC 0.94-0.97 across all four detectors is suspiciously good. When every model — density-based, tree-based, boundary-based, Gaussian-based — agrees
that strongly, it usually means the dataset has a structural shortcut that any classifier can exploit, not that the models are good. The synthetic
generator puts obvious anomalies at 12-22 Action nodes while normals sit at 1-10. That is a gap so wide any 64-d embedding trivially separates them.

Conclusion: the synthetic-only numbers are not evidence that the approach works. They're evidence that the synthetic dataset is easier than real IAM.

2. Real-only is basically coin flips

ROC-AUC 0.46–0.58 across the board. But here the problem is partly sample size — only 4 anomalies in the real workbook's label set (AdministratorAccess,
PowerUserAccess, AWSCodeStarServiceRole, AWSOpsWorksRegisterCLI). With 4 positives, one miss swings recall from 0.75 to 0.50. The metrics are too noisy to
conclude much. What we can say is that none of the four models got above 0.58 ROC-AUC, so even with proper labels the representation is not clearly
useful here.

3. Merged is where the truth shows up

This is the most informative run because it has both statistical weight (29 anomalies, 111 test samples) and realistic normal distribution (mix of custom

- AWS-managed). Every ROC-AUC lands in 0.43–0.48 — below or at random. The F1=0.32 from OneClassSVM is just wide-net over-alerting (precision 0.24).

The merged collapse is exactly what my earlier analysis predicted: real AWS-managed policies (413 of 515 use Resource: _, many use service:_ actions) are
structurally indistinguishable from the synthetic "obvious" anomalies. Once they enter the training set, the detectors learn "many actions on wildcards"
is the shape of normal, and the obvious anomalies lose their geometric signature.

4. The synth→merged delta is the central empirical result

Look at the ROC-AUC drop for each model when real data is added:

┌───────┬─────────────────────┬─────────────────────────────────┐
│ Model │ Synth-only → Merged │ Meaning │
├───────┼─────────────────────┼─────────────────────────────────┤
│ IF │ 0.96 → 0.48 (-0.48) │ ranking completely destroyed │
├───────┼─────────────────────┼─────────────────────────────────┤
│ LOF │ 0.97 → 0.43 (-0.54) │ ranking destroyed, below random │
├───────┼─────────────────────┼─────────────────────────────────┤
│ OCSVM │ 0.96 → 0.45 (-0.51) │ ranking destroyed │
├───────┼─────────────────────┼─────────────────────────────────┤
│ EE │ 0.94 → 0.44 (-0.50) │ ranking destroyed │
└───────┴─────────────────────┴─────────────────────────────────┘

A uniform -0.5 drop across all four models is a distribution-shift signal, not a model signal. The feature space was only separable because synthetic
normals were too narrow. Real IAM usage widens the normal cloud to fully overlap the anomaly cloud.

5. What this actually proves

- Node2Vec on the IAM graph is content-blind. It cannot tell "wildcard-heavy by design" (AWSBackupAdminPolicy) from "wildcard-heavy by misconfiguration"
  (AdministratorAccess attached where it shouldn't be). They have the same graph shape.
- The synthetic dataset overstates the method's effectiveness. Any result reported on synthetic-only should be labeled as such.
- Pure unsupervised anomaly detection on topology embeddings is not a viable production approach for this problem. Even if you retuned contamination and
  nu, the underlying ROC-AUC in the realistic regime is below random.
- The 4 real-dataset anomalies are too few to evaluate. If you want a real-data evaluation, you need a larger labeled set — either by labeling suspicious
  real policies by hand, or by planting synthetic anomalies into the real workbook rather than side-by-side.

6. What to do next

Short term (validate the diagnosis):

1. Run compare_datasets.py on merged vs real — the wildcard-action ratio should show real at ~32% and merged slightly lower. Confirms the overlap
   hypothesis.
2. Look at outputs/predictions/isolation_forest_pred.csv for the merged run: check whether the top-scored "anomalies" are the AWS-managed ones. If yes,
   the model is pattern-matching on wildcard topology exactly as predicted.

Medium term (fix the representation):
Add a semantic feature vector alongside the Node2Vec embedding. Concrete 12 flags that would most likely recover signal:

- is_aws_managed (ARN prefix arn:aws:iam::aws:policy/)
- has_action_star (literal "\*")
- wildcard_action_ratio (fraction of actions matching _:_ or \*)
- has_resource_star
- cross_account_arn_present (any arn:aws:...::\*:... or third-party account)
- has_not_action
- has_iam_passrole
- has_sts_assumerole
- has_iam_create_or_attach_policy (privilege escalation combo)
- trust_policy_principal_star (roles only)
- num_distinct_services (breadth proxy)
- num_statements

Concatenate with the 64-d Node2Vec vector → 76-d feature. Retrain. I'd expect the merged-dataset ROC-AUC to jump from 0.45 → 0.75-0.85, because now the
detectors have both shape (from Node2Vec) and content (from flags).

Long term (honest evaluation):
Plant the 12 synthetic subtle anomalies into the real workbook — give them real-looking names and embed them in the same PolicyObject format. This creates
a realistic test: the detector has to find misconfigurations hidden among 500 legitimate AWS-managed policies, not surrounded by a synthetic narrow
normal class. That run is the honest benchmark.

Bottom line

The three runs form a clean A/B/C:

- A (synth): too easy → results are illusory
- B (real): too few labels → results are noise
- C (merged): realistic distribution → results show the method doesn't work as-is

That merged run is actually the most valuable thing you've produced so far, because it lets you defensibly say "Node2Vec-based topological anomaly
detection fails on realistic IAM corpora, and here is why." That's a publishable negative result if you frame it right — it motivates the semantic-feature
extension as a necessary fix, not an optional improvement.
