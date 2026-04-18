# Dataset Side-by-Side Analysis

**Fidelity score: 86.2/100**  _(higher = synthetic closer to real)_


## Policies

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                  515 |                  325 |  |
| Labeled anomalies (n / %)                |             4 / 0.8% |            25 / 7.7% | (!) gap=0.07 |
| AttachmentCount mean                     |                0.008 |                0.151 | ok |
| AttachmentCount median                   |                0.000 |                0.000 | ok |
| AttachmentCount max                      |                    3 |                    3 |  |
| Statements / policy (mean)               |                2.258 |                1.889 | ok |
| Statements / policy (median)             |                1.000 |                2.000 |  |
| Statements / policy (max)                |                   19 |                    3 |  |
| Wildcard-action policies                 |          168 (32.6%) |          120 (36.9%) | ok |
| Wildcard-resource policies               |          495 (96.1%) |            18 (5.5%) |  |
| Allow ratio (of all stmts)               |                0.996 |                1.000 | ok |
| Deny ratio (of all stmts)                |                0.004 |                0.000 |  |

### Path Distribution (policies)

| Path | Real | Synthetic |
| --- | --- | --- |
| `/` | 66.6% | 36.6% |
| `/aws-service-role/` | 14.4% | 30.5% |
| `/job-function/` | 1.4% | — |
| `/service-role/` | 17.7% | 32.9% |

### DefaultVersionId Distribution (policies)

| VersionId | Real | Synthetic |
| --- | --- | --- |
| `v1` | 55.1% | — |
| `v2` | 17.7% | 18.2% |
| `v3` | 10.3% | 18.8% |
| `v4` | 5.2% | 20.0% |
| `v6` | — | 16.3% |


## Users

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                    1 |                  200 |  |
| AttachedPolicies mean                    |                2.000 |                2.825 | ok |
| AttachedPolicies median                  |                2.000 |                3.000 |  |
| AttachedPolicies max                     |                    2 |                    5 |  |
| Users with 0 policies (%)                |                 0.0% |                 0.0% |  |


## Groups

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                    1 |                   25 |  |
| AttachedPolicies mean                    |                0.000 |                2.640 |  |
| AttachedPolicies max                     |                    0 |                    6 |  |
| Users per group mean                     |                1.000 |                8.160 |  |
| Users per group max                      |                    1 |                   15 |  |
| Groups with 0 policies (%)               |               100.0% |                16.0% |  |
| Groups with 0 users (%)                  |                 0.0% |                 8.0% |  |


## Roles

| Metric                                   |                 Real |            Synthetic | Note |
| ---------------------------------------- | -------------------- | -------------------- |  |
| Row count                                |                    1 |                   40 |  |
| AttachedPolicies mean                    |                1.000 |                3.275 |  |
| AttachedPolicies median                  |                1.000 |                3.000 |  |
| AttachedPolicies max                     |                    1 |                    6 |  |
| Roles with 0 policies (%)                |                 0.0% |                 0.0% |  |

## Fidelity Breakdown

- Anomaly ratio gap 6.9pp (real=0.8%, syn=7.7%) -14pts

## Recommendations

1. **Wildcard actions are overrepresented** in synthetic data. In the real dataset no normal policy uses `*` actions — keep wildcards exclusively in labeled anomaly rows.
