# Dataset Comparison Summary

- Left workbook: `data/dataset.xlsx`
- Right workbook: `data/syntheticdataset/syntheticDataset.xlsx`
- Same sheet set: `True`
- Exact raw schema match: `False`
- Compatible with required pipeline schema: `True`

## Sheet Comparison

### groups

- Row counts: left=1, right=20
- Exact same columns: `False`
- Left-only columns: Path, Unnamed: 0
- Right-only columns: none
- Left feature candidates: AttachedPolicies, Users
- Left identifier-like: Path, GroupName, GroupId, Arn
- Left `AttachedPolicies` token counts: min=0, median=0.0, mean=0.0, max=0
- Left `Users` token counts: min=1, median=1.0, mean=1.0, max=1
- Right feature candidates: AttachedPolicies, Users
- Right identifier-like: GroupName, GroupId, Arn
- Right `AttachedPolicies` token counts: min=1, median=4.0, mean=4.75, max=10
- Right `Users` token counts: min=13, median=20.0, mean=19.75, max=28

### policies

- Row counts: left=515, right=300
- Exact same columns: `False`
- Left-only columns: AttachmentCount, CreateDate, DefaultVersionId, Path, Unnamed: 0, UpdateDate
- Right-only columns: none
- Left feature candidates: Path, AttachmentCount, CreateDate, UpdateDate, PolicyObject
- Left identifier-like: PolicyName, PolicyId, Arn, DefaultVersionId
- Left `AttachmentCount` numeric stats: min=0, median=0.0, mean=0.0078, max=3
- Right feature candidates: PolicyObject
- Right identifier-like: PolicyName, PolicyId, Arn

### roles

- Row counts: left=1, right=30
- Exact same columns: `False`
- Left-only columns: AssumeRolePolicyDocument, CreateDate, Path, Unnamed: 0
- Right-only columns: none
- Left feature candidates: CreateDate, AssumeRolePolicyDocument, AttachedPolicies
- Left identifier-like: Path, RoleName, RoleId, Arn
- Left `AttachedPolicies` token counts: min=1, median=1.0, mean=1.0, max=1
- Right feature candidates: AttachedPolicies
- Right identifier-like: RoleName, RoleId, Arn
- Right `AttachedPolicies` token counts: min=1, median=4.5, mean=4.4, max=8

### users

- Row counts: left=1, right=200
- Exact same columns: `False`
- Left-only columns: CreateDate, Path, Unnamed: 0
- Right-only columns: none
- Left feature candidates: CreateDate, AttachedPolicies
- Left identifier-like: Path, UserName, UserId, Arn
- Left `AttachedPolicies` token counts: min=2, median=2.0, mean=2.0, max=2
- Right feature candidates: AttachedPolicies
- Right identifier-like: UserName, UserId, Arn
- Right `AttachedPolicies` token counts: min=1, median=2.0, mean=2.0071, max=3

## ML Notes

- `identifier_like_columns` are usually poor raw tabular features and should be encoded, dropped, or replaced with engineered statistics.
- `list_like` fields such as `AttachedPolicies` and `Users` are more useful through counts, cardinalities, or graph-derived features than raw strings.
- If `required_schema_match` is true but `exact_schema_match` is false, both files fit the current pipeline contract but are not identical raw exports.
