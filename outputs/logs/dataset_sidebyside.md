# Dataset Side-by-Side Analysis

**Fidelity score: 100.0/100**  _(higher = synthetic closer to real)_


## Policies

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                  840 |                  325 |  |
| Labeled anomalies (n / %)                |            29 / 3.5% |            25 / 7.7% | ok |
| AttachmentCount mean                     |                0.070 |                0.169 | ok |
| AttachmentCount median                   |                0.000 |                0.000 | ok |
| AttachmentCount max                      |                    3 |                    3 |  |
| Statements / policy (mean)               |                2.165 |                2.018 | ok |
| Statements / policy (median)             |                2.000 |                2.000 |  |
| Statements / policy (max)                |                   19 |                    5 |  |
| Wildcard-action policies                 |          276 (32.9%) |          108 (33.2%) | ok |
| Wildcard-resource policies               |          513 (61.1%) |            18 (5.5%) |  |
| Allow ratio (of all stmts)               |                0.997 |                1.000 | ok |
| Deny ratio (of all stmts)                |                0.003 |                0.000 |  |

### Path Distribution (policies)

| Path | Real | Synthetic |
| --- | --- | --- |
| `/` | 54.8% | 36.0% |
| `/aws-service-role/` | 21.2% | 32.0% |
| `/job-function/` | 0.8% | — |
| `/service-role/` | 23.2% | 32.0% |

### DefaultVersionId Distribution (policies)

| VersionId | Real | Synthetic |
| --- | --- | --- |
| `v1` | 39.4% | — |
| `v2` | 17.9% | 18.2% |
| `v3` | 12.9% | 16.9% |
| `v4` | 10.5% | 18.8% |
| `v6` | — | 17.5% |


## Users

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                  201 |                  200 |  |
| AttachedPolicies mean                    |                2.811 |                2.815 | ok |
| AttachedPolicies median                  |                3.000 |                3.000 |  |
| AttachedPolicies max                     |                    5 |                    5 |  |
| Users with 0 policies (%)                |                 0.0% |                 0.0% |  |


## Groups

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                   26 |                   25 |  |
| AttachedPolicies mean                    |                2.500 |                2.600 |  |
| AttachedPolicies max                     |                    6 |                    6 |  |
| Users per group mean                     |                7.192 |                7.440 |  |
| Users per group max                      |                   14 |                   14 |  |
| Groups with 0 policies (%)               |                19.2% |                16.0% |  |
| Groups with 0 users (%)                  |                 7.7% |                 8.0% |  |


## Roles

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                   41 |                   40 |  |
| AttachedPolicies mean                    |                3.000 |                3.050 |  |
| AttachedPolicies median                  |                3.000 |                3.000 |  |
| AttachedPolicies max                     |                    6 |                    6 |  |
| Roles with 0 policies (%)                |                 0.0% |                 0.0% |  |

## Fidelity Breakdown

- No major gaps detected.

## Recommendations

1. Synthetic dataset is a reasonable structural match. Focus next on embedding-space overlap verification.
