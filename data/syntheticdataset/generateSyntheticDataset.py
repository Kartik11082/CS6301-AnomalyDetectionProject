"""Generate a synthetic IAM workbook that matches data/dataset.xlsx schema.

Output: syntheticDataset.xlsx with sheets: policies, users, groups, roles.

Anomaly taxonomy (both groups are labeled via config/data.yaml):
  OBVIOUS  — literal Action="*" or Action="svc:*" on Resource="*"
  SUBTLE   — broad glob prefixes (s3:Get*), cross-account ARNs,
             dangerous action combos, NotAction privilege-escalation,
             overly-permissive trust expressed as sts:AssumeRole grants
"""

from __future__ import annotations

import hashlib
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
SEED = 42
NUM_USERS = 200
NUM_GROUPS = 25
NUM_ROLES = 40
NUM_NORMAL_POLICIES = 300

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DATA_CFG = PROJECT_ROOT / "config" / "data.yaml"
OUT_XLSX = HERE / "syntheticDataset.xlsx"

random.seed(SEED)

SERVICES = ["s3", "ec2", "lambda", "dynamodb", "iam", "sts", "logs", "kms", "sqs", "sns"]
ACTIONS_BY_SVC: dict[str, list[str]] = {
    "s3":       ["GetObject", "PutObject", "ListBucket", "DeleteObject", "GetBucketAcl"],
    "ec2":      ["DescribeInstances", "StartInstances", "StopInstances", "RunInstances", "DescribeSecurityGroups"],
    "lambda":   ["InvokeFunction", "GetFunction", "ListFunctions", "UpdateFunctionCode"],
    "dynamodb": ["GetItem", "PutItem", "Query", "Scan", "DeleteItem"],
    "iam":      ["GetRole", "ListRoles", "GetUser", "ListUsers", "GetPolicy"],
    "sts":      ["GetCallerIdentity"],
    "logs":     ["CreateLogGroup", "PutLogEvents", "DescribeLogStreams", "FilterLogEvents"],
    "kms":      ["Encrypt", "DescribeKey", "ListKeys"],
    "sqs":      ["SendMessage", "ReceiveMessage", "DeleteMessage", "GetQueueAttributes"],
    "sns":      ["Publish", "Subscribe", "ListTopics"],
}
OWN_ACCOUNT = "123456789012"
EXT_ACCOUNTS = ["987654321098", "111122223333", "444455556666"]


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def rand_hex(n: int) -> str:
    return "".join(random.choices(string.hexdigits.lower()[:16], k=n))


def rand_id(prefix: str, length: int = 20) -> str:
    return prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def rand_date(start_year: int = 2018, end_year: int = 2024) -> str:
    start = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    end   = datetime(end_year, 12, 31, tzinfo=timezone.utc)
    delta = end - start
    dt = start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def pystr_list(obj) -> str:
    return repr(obj)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def own_arn(svc: str, resource: str) -> str:
    return f"arn:aws:{svc}:us-east-1:{OWN_ACCOUNT}:{resource}"


# ---------------------------------------------------------------------------
# NORMAL statement — scoped actions on scoped resources
# ---------------------------------------------------------------------------
def normal_statement() -> dict:
    svc = random.choice(SERVICES)
    n_actions = random.randint(1, 4)
    actions = [f"{svc}:{a}" for a in random.sample(ACTIONS_BY_SVC[svc], k=min(n_actions, len(ACTIONS_BY_SVC[svc])))]
    resource = own_arn(svc, f"resource-{random.randint(1, 9999)}/{rand_hex(6)}")
    return {"Action": actions, "Effect": "Allow", "Resource": [resource]}


# ---------------------------------------------------------------------------
# OBVIOUS anomaly statements — structurally extreme so Node2Vec can tell them
# apart from normal policies by graph topology alone (high action-node degree).
#
# Each obvious policy gets one statement listing ALL actions across ALL services
# (~40 Action nodes) connected to a wildcard resource — far outside the normal
# range of 1–12 Action nodes per policy.  This mirrors real "AdminAccess"-style
# over-permission, keeps the wildcard semantics, and gives the embedding a clear
# structural signal without relying on node-name content.
# ---------------------------------------------------------------------------
def obvious_misconfigured_statements() -> list:
    # 3–5 statements, each covering a random service's FULL action list on "*".
    # This gives 12–25 distinct Action nodes per policy — well above the normal
    # range of 1–12 — while keeping each policy structurally unique (different
    # service subsets) so they don't collapse into a dense cluster.
    n_svcs = random.randint(3, 5)
    chosen = random.sample(SERVICES, k=n_svcs)
    stmts = []
    for svc in chosen:
        stmts.append({
            "Action": [f"{svc}:{a}" for a in ACTIONS_BY_SVC[svc]],
            "Effect": "Allow",
            "Resource": ["*"],
        })
    return stmts


# ---------------------------------------------------------------------------
# SUBTLE anomaly statements — harder to catch, no literal "*" in Action
# ---------------------------------------------------------------------------
def subtle_misconfigured_statements(name: str) -> list:
    """
    Each subtle name maps to a specific pattern so re-running is reproducible.
    Patterns are chosen by name to make each labeled policy structurally distinct.
    """
    dispatch: dict[str, callable] = {
        "tf-s3-reader-all-buckets":          _subtle_s3_all_buckets,
        "tf-ec2-describe-all":               _subtle_ec2_describe_all,
        "tf-iam-user-management-broad":      _subtle_iam_user_mgmt,
        "tf-sts-cross-account-assume":       _subtle_cross_account_assume,
        "tf-lambda-deploy-broad":            _subtle_lambda_deploy,
        "tf-kms-decrypt-broad":              _subtle_kms_decrypt,
        "tf-data-pipeline-passrole":         _subtle_passrole_combo,
        "tf-logs-reader-all-accounts":       _subtle_logs_all_accounts,
        "tf-notaction-restricted-allow":     _subtle_notaction,
        "tf-ssm-parameter-access-all":       _subtle_ssm_all,
        "tf-secretsmanager-rotation-broad":  _subtle_secretsmanager,
        "tf-dynamodb-cross-account-stream":  _subtle_dynamodb_cross_account,
    }
    fn = dispatch.get(name)
    if fn is None:
        return _subtle_s3_all_buckets()
    return fn()


def _subtle_s3_all_buckets() -> list:
    # Broad prefix action on all S3 resources — not `*` but effectively reads any bucket
    return [{"Action": ["s3:GetObject", "s3:GetBucketAcl", "s3:ListBucket"],
             "Effect": "Allow",
             "Resource": ["arn:aws:s3:::*"]}]


def _subtle_ec2_describe_all() -> list:
    # Glob-prefix action on wildcard resource
    return [{"Action": ["ec2:Describe*"],
             "Effect": "Allow",
             "Resource": ["*"]}]


def _subtle_iam_user_mgmt() -> list:
    # Dangerous combination: create user + attach policy + create key — all on *
    return [{"Action": ["iam:CreateUser", "iam:AttachUserPolicy",
                        "iam:CreateAccessKey", "iam:CreateLoginProfile"],
             "Effect": "Allow",
             "Resource": ["*"]}]


def _subtle_cross_account_assume() -> list:
    # sts:AssumeRole on any role in any account — cross-account pivot
    ext = random.choice(EXT_ACCOUNTS)
    return [{"Action": ["sts:AssumeRole"],
             "Effect": "Allow",
             "Resource": [f"arn:aws:iam::*:role/*",
                          f"arn:aws:iam::{ext}:role/AdminRole"]}]


def _subtle_lambda_deploy() -> list:
    # Lambda code injection path: PassRole + CreateFunction + InvokeFunction on *
    return [{"Action": ["iam:PassRole", "lambda:CreateFunction",
                        "lambda:InvokeFunction", "lambda:UpdateFunctionCode"],
             "Effect": "Allow",
             "Resource": ["*"]}]


def _subtle_kms_decrypt() -> list:
    # Decrypt on all KMS keys across all regions — broad but not wildcard action
    return [{"Action": ["kms:Decrypt", "kms:GenerateDataKey"],
             "Effect": "Allow",
             "Resource": [f"arn:aws:kms:*:{OWN_ACCOUNT}:key/*",
                          f"arn:aws:kms:*:*:key/*"]}]


def _subtle_passrole_combo() -> list:
    # Data pipeline privilege escalation: PassRole + Glue/Step job creation
    return [
        {"Action": ["iam:PassRole"], "Effect": "Allow",
         "Resource": [f"arn:aws:iam::{OWN_ACCOUNT}:role/*"]},
        {"Action": ["glue:CreateJob", "glue:StartJobRun",
                    "states:CreateStateMachine", "states:StartExecution"],
         "Effect": "Allow",
         "Resource": ["*"]},
    ]


def _subtle_logs_all_accounts() -> list:
    # Read logs from any account/region — cross-account log access
    return [{"Action": ["logs:FilterLogEvents", "logs:GetLogEvents",
                        "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
             "Effect": "Allow",
             "Resource": ["arn:aws:logs:*:*:log-group:*",
                          "arn:aws:logs:*:*:log-group:*:log-stream:*"]}]


def _subtle_notaction() -> list:
    # NotAction privilege escalation: Allow everything EXCEPT a narrow deny list
    return [{"NotAction": ["iam:DeletePolicy", "iam:DeleteRole", "iam:DeleteUser"],
             "Effect": "Allow",
             "Resource": ["*"]}]


def _subtle_ssm_all() -> list:
    # Read any SSM parameter (including SecureString secrets) in all accounts
    return [{"Action": ["ssm:GetParameter", "ssm:GetParameters",
                        "ssm:GetParametersByPath", "ssm:DescribeParameters"],
             "Effect": "Allow",
             "Resource": ["arn:aws:ssm:*:*:parameter/*"]}]


def _subtle_secretsmanager() -> list:
    # Rotation role that can read/rotate any secret
    return [{"Action": ["secretsmanager:GetSecretValue", "secretsmanager:RotateSecret",
                        "secretsmanager:ListSecrets"],
             "Effect": "Allow",
             "Resource": ["arn:aws:secretsmanager:*:*:secret:*"]}]


def _subtle_dynamodb_cross_account() -> list:
    # Stream consumer that can read any table's stream in any account
    ext = random.choice(EXT_ACCOUNTS)
    return [{"Action": ["dynamodb:GetRecords", "dynamodb:GetShardIterator",
                        "dynamodb:DescribeStream", "dynamodb:ListStreams"],
             "Effect": "Allow",
             "Resource": [f"arn:aws:dynamodb:*:{ext}:table/*/stream/*",
                          f"arn:aws:dynamodb:*:*:table/*/stream/*"]}]


# ---------------------------------------------------------------------------
# LOAD LABEL CONFIG
# ---------------------------------------------------------------------------
with open(DATA_CFG, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

ALL_LABELED = list(cfg.get("misconfigured_policies_by_name", []))

OBVIOUS_NAMES = {
    "tf-secmon-iam-policy",
    "tf-splunk-ingestion-aws-addon-policy-master20200917101618881200000005",
    "tf-customconfig-policy-master",
    "tf-ds-tscm-lambda-policy",
    "tf-aws-team-cf-cr",
    "awt-role-boundary",
    "awt-user-boundary",
    "CloudabilityPolicy",
    "PowerUserAccess",
    "AdministratorAccess",
    "AWSOpsWorksRegisterCLI",
    "AWSCodeStarServiceRole",
    "AWSApplicationMigrationReplicationServerPolicy",
}
SUBTLE_NAMES = set(ALL_LABELED) - OBVIOUS_NAMES


# ---------------------------------------------------------------------------
# ROW BUILDER
# ---------------------------------------------------------------------------
def make_policy_row(idx: int, name: str) -> dict:
    pid = rand_id("A", 20)
    path = random.choice(["/", "/service-role/", "/aws-service-role/"])
    arn = f"arn:aws:iam::aws:policy{path}{name}"

    if name in OBVIOUS_NAMES:
        statements = obvious_misconfigured_statements()
    elif name in SUBTLE_NAMES:
        statements = subtle_misconfigured_statements(name)
    else:
        n = random.randint(1, 3)
        # ~32% of normal policies contain one service-level wildcard stmt (matches real dataset)
        has_wildcard_stmt = random.random() < 0.32
        statements = []
        for j in range(n):
            if j == 0 and has_wildcard_stmt:
                svc = random.choice(SERVICES)
                statements.append({"Action": [f"{svc}:*"], "Effect": "Allow",
                                   "Resource": [own_arn(svc, "resource/*")]})
            else:
                statements.append(normal_statement())

    return {
        "Unnamed: 0": idx,
        "PolicyName": name,
        "PolicyId": pid,
        "Arn": arn,
        "Path": path,
        "DefaultVersionId": f"v{random.randint(1, 6)}",
        "AttachmentCount": 0 if random.random() < 0.92 else random.randint(1, 3),
        "CreateDate": rand_date(),
        "UpdateDate": rand_date(),
        "PolicyObject": pystr_list(statements),
    }


# ---------------------------------------------------------------------------
# GENERATE POLICIES
# ---------------------------------------------------------------------------
policies = []
for idx, name in enumerate(ALL_LABELED):
    policies.append(make_policy_row(idx, name))

normal_name_pool = [
    "AmazonS3ReadOnlyAccess", "AmazonEC2ReadOnlyAccess", "AWSLambdaBasicExecutionRole",
    "AmazonDynamoDBReadOnlyAccess", "CloudWatchLogsReadOnlyAccess", "AmazonSQSReadOnlyAccess",
    "AmazonSNSReadOnlyAccess", "AWSKeyManagementServicePowerUser", "IAMReadOnlyAccess",
    "AmazonRDSReadOnlyAccess", "AWSCloudTrailReadOnlyAccess", "AmazonVPCReadOnlyAccess",
]
base_idx = len(ALL_LABELED)
for i in range(NUM_NORMAL_POLICIES):
    name = f"{random.choice(normal_name_pool)}-{i:04d}"
    policies.append(make_policy_row(base_idx + i, name))

policies_df = pd.DataFrame(policies)
policy_refs = [{"PolicyName": p["PolicyName"], "PolicyArn": p["Arn"]} for p in policies]

# ---------------------------------------------------------------------------
# GENERATE USERS
# ---------------------------------------------------------------------------
users = []
for i in range(NUM_USERS):
    k = random.randint(1, 5)
    users.append({
        "Unnamed: 0": i,
        "Path": "/",
        "UserName": sha256_hex(f"user-{i}"),
        "UserId":   sha256_hex(f"uid-{i}"),
        "Arn":      sha256_hex(f"uarn-{i}"),
        "CreateDate": rand_date(),
        "AttachedPolicies": pystr_list(random.sample(policy_refs, k=k)),
    })
users_df = pd.DataFrame(users)

# ---------------------------------------------------------------------------
# GENERATE GROUPS
# ---------------------------------------------------------------------------
groups = []
for i in range(NUM_GROUPS):
    n_pol  = random.randint(0, 6)
    n_usr  = random.randint(0, 15)
    member_refs = [
        {"UserName": users[j]["UserName"], "UserId": users[j]["UserId"], "Arn": users[j]["Arn"]}
        for j in (random.sample(range(NUM_USERS), k=n_usr) if n_usr else [])
    ]
    groups.append({
        "Unnamed: 0": i,
        "Path": "/",
        "GroupName": sha256_hex(f"group-{i}"),
        "GroupId":   sha256_hex(f"gid-{i}"),
        "Arn":       sha256_hex(f"garn-{i}"),
        "AttachedPolicies": pystr_list(random.sample(policy_refs, k=n_pol) if n_pol else []),
        "Users": pystr_list(member_refs),
    })
groups_df = pd.DataFrame(groups)

# ---------------------------------------------------------------------------
# GENERATE ROLES
# ---------------------------------------------------------------------------
# Normal trust document — scoped to own account root
NORMAL_TRUST = (
    '{\n    "Version": "2012-10-17",\n    "Statement": {\n        "Effect": "Allow",\n'
    f'        "Principal": {{"AWS": "arn:aws:iam::{OWN_ACCOUNT}:root"}},\n'
    '        "Action": "sts:AssumeRole"\n    }\n}\n'
)
# Overly-permissive trust — principal is * (used for ~15% of roles to add noise)
BROAD_TRUST = (
    '{\n    "Version": "2012-10-17",\n    "Statement": {\n        "Effect": "Allow",\n'
    '        "Principal": {"AWS": "*"},\n'
    '        "Action": "sts:AssumeRole"\n    }\n}\n'
)

roles = []
for i in range(NUM_ROLES):
    trust = BROAD_TRUST if random.random() < 0.15 else NORMAL_TRUST
    n_pol = random.randint(1, 6)
    roles.append({
        "Unnamed: 0": i,
        "Path": "/",
        "RoleName": sha256_hex(f"role-{i}"),
        "RoleId":   sha256_hex(f"rid-{i}"),
        "Arn":      sha256_hex(f"rarn-{i}"),
        "CreateDate": rand_date(),
        "AssumeRolePolicyDocument": trust,
        "AttachedPolicies": pystr_list(random.sample(policy_refs, k=n_pol)),
    })
roles_df = pd.DataFrame(roles)

# ---------------------------------------------------------------------------
# WRITE WORKBOOK
# ---------------------------------------------------------------------------
with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
    policies_df.to_excel(writer, sheet_name="policies", index=False)
    users_df.to_excel(writer, sheet_name="users",    index=False)
    groups_df.to_excel(writer, sheet_name="groups",  index=False)
    roles_df.to_excel(writer, sheet_name="roles",    index=False)

n_obvious = len(OBVIOUS_NAMES)
n_subtle  = len(SUBTLE_NAMES)
print(f"Wrote {OUT_XLSX}")
print(f"  policies : {len(policies_df)}  (obvious anomalies={n_obvious}, subtle={n_subtle}, normal={NUM_NORMAL_POLICIES})")
print(f"  users    : {len(users_df)}")
print(f"  groups   : {len(groups_df)}")
print(f"  roles    : {len(roles_df)}  (broad trust in ~15%)")
print()
print("Subtle anomaly patterns:")
for name in sorted(SUBTLE_NAMES):
    print(f"  {name}")
