# Dataset Comparison Summary

- Left workbook: `data/dataset.xlsx`
- Right workbook: `data/syntheticdataset/syntheticDataset.xlsx`
- Same sheet set: `True`
- Exact raw schema match: `True`
- Compatible with required pipeline schema: `True`

## Sheet Comparison

### groups
- Row counts: left=1, right=25
- Exact same columns: `True`
- Left-only columns: none
- Right-only columns: none
- Left feature candidates: AttachedPolicies, Users
- Left identifier-like: Path, GroupName, GroupId, Arn
- Left `AttachedPolicies` token counts: min=0, median=0.0, mean=0.0, max=0
- Left `Users` token counts: min=1, median=1.0, mean=1.0, max=1
- Right feature candidates: Path, AttachedPolicies, Users
- Right identifier-like: GroupName, GroupId, Arn
- Right `AttachedPolicies` token counts: min=0, median=2.0, mean=2.6, max=6
- Right `Users` token counts: min=1, median=7.0, mean=7.16, max=15

### policies
- Row counts: left=515, right=313
- Exact same columns: `True`
- Left-only columns: none
- Right-only columns: none
- Left feature candidates: Path, AttachmentCount, CreateDate, UpdateDate, PolicyObject
- Left identifier-like: PolicyName, PolicyId, Arn, DefaultVersionId
- Left `AttachmentCount` numeric stats: min=0, median=0.0, mean=0.0078, max=3
- Right feature candidates: Path, AttachmentCount, CreateDate, UpdateDate, PolicyObject
- Right identifier-like: PolicyName, PolicyId, Arn, DefaultVersionId
- Right `AttachmentCount` numeric stats: min=0, median=13.0, mean=12.6294, max=25

### roles
- Row counts: left=1, right=40
- Exact same columns: `True`
- Left-only columns: none
- Right-only columns: none
- Left feature candidates: CreateDate, AssumeRolePolicyDocument, AttachedPolicies
- Left identifier-like: Path, RoleName, RoleId, Arn
- Left `AttachedPolicies` token counts: min=1, median=1.0, mean=1.0, max=1
- Right feature candidates: Path, CreateDate, AssumeRolePolicyDocument, AttachedPolicies
- Right identifier-like: RoleName, RoleId, Arn
- Right `AttachedPolicies` token counts: min=1, median=4.0, mean=3.7, max=6

### users
- Row counts: left=1, right=200
- Exact same columns: `True`
- Left-only columns: none
- Right-only columns: none
- Left feature candidates: CreateDate, AttachedPolicies
- Left identifier-like: Path, UserName, UserId, Arn
- Left `AttachedPolicies` token counts: min=2, median=2.0, mean=2.0, max=2
- Right feature candidates: Path, CreateDate, AttachedPolicies
- Right identifier-like: UserName, UserId, Arn
- Right `AttachedPolicies` token counts: min=1, median=3.0, mean=2.985, max=5

## ML Notes

- `identifier_like_columns` are usually poor raw tabular features and should be encoded, dropped, or replaced with engineered statistics.
- `list_like` fields such as `AttachedPolicies` and `Users` are more useful through counts, cardinalities, or graph-derived features than raw strings.
- If `required_schema_match` is true but `exact_schema_match` is false, both files fit the current pipeline contract but are not identical raw exports.
