# Time-Series Online Update Simulation

- Base dataset: `data/syntheticdataset/syntheticDataset.xlsx`
- Snapshot directory: `data/timeseries`
- Steps: `4`

| step | added | deleted | document changed | metadata changed |
| --- | --- | --- | --- | --- |
| 1 | online-added-policy-step-01 | AmazonEC2ReadOnlyAccess-0000 | IAMReadOnlyAccess-0001 | AWSKeyManagementServicePowerUser-0002 |
| 2 | online-added-policy-step-02 | AWSKeyManagementServicePowerUser-0002 | AmazonSNSReadOnlyAccess-0003 | IAMReadOnlyAccess-0004 |
| 3 | online-added-policy-step-03 | IAMReadOnlyAccess-0004 | AWSLambdaBasicExecutionRole-0005 | AmazonVPCReadOnlyAccess-0006 |
| 4 | online-added-policy-step-04 | AmazonVPCReadOnlyAccess-0006 | AmazonS3ReadOnlyAccess-0007 | AmazonSNSReadOnlyAccess-0008 |

## Replay With Neo4j

Each generated config can be used with the existing update command, for example:

```bash
python -m src.pipeline update --old-data-config config/timeseries/snapshot_00.yaml --new-data-config config/timeseries/snapshot_01.yaml
```
