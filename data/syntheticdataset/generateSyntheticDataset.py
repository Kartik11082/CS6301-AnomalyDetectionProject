import json
import random
import string
import pandas as pd

# ------------------------
# CONFIG
# ------------------------
NUM_USERS = 200
NUM_GROUPS = 20
NUM_ROLES = 30
NUM_POLICIES = 300
ANOMALY_RATIO = 0.05

SERVICES = ["s3", "ec2", "lambda", "dynamodb", "iam"]
ACTIONS = ["GetObject", "PutObject", "ListBucket", "StartInstances", "InvokeFunction"]


# ------------------------
# HELPERS
# ------------------------
def random_id(prefix):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def random_arn(service):
    return f"arn:aws:{service}:::resource/{random.randint(1,1000)}"


def generate_policy(anomaly=False):
    service = random.choice(SERVICES)

    if anomaly:
        # Inject bad patterns
        return {
            "Effect": "Allow",
            "Action": "*" if random.random() < 0.7 else f"{service}:*",
            "Resource": "*",
        }
    else:
        return {
            "Effect": "Allow",
            "Action": f"{service}:{random.choice(ACTIONS)}",
            "Resource": random_arn(service),
        }


# ------------------------
# GENERATE POLICIES
# ------------------------
policies = []
policy_ids = []

for i in range(NUM_POLICIES):
    pid = random_id("pol-")
    is_anomaly = random.random() < ANOMALY_RATIO

    policy = generate_policy(anomaly=is_anomaly)

    policies.append(
        {
            "PolicyName": f"Policy_{i}",
            "PolicyId": pid,
            "Arn": f"arn:aws:iam::policy/{pid}",
            "PolicyObject": json.dumps(policy),
        }
    )

    policy_ids.append(pid)

policies_df = pd.DataFrame(policies)

# ------------------------
# GENERATE GROUPS
# ------------------------
groups = []
group_ids = []

for i in range(NUM_GROUPS):
    gid = random_id("grp-")

    attached = random.sample(policy_ids, random.randint(1, 10))

    groups.append(
        {
            "GroupName": f"Group_{i}",
            "GroupId": gid,
            "Arn": f"arn:aws:iam::group/{gid}",
            "AttachedPolicies": ",".join(attached),
            "Users": "",  # fill later
        }
    )

    group_ids.append(gid)

groups_df = pd.DataFrame(groups)

# ------------------------
# GENERATE USERS
# ------------------------
users = []

for i in range(NUM_USERS):
    uid = random_id("usr-")

    attached = random.sample(policy_ids, random.randint(0, 3))
    assigned_groups = random.sample(group_ids, random.randint(1, 3))

    users.append(
        {
            "UserName": f"User_{i}",
            "UserId": uid,
            "Arn": f"arn:aws:iam::user/{uid}",
            "AttachedPolicies": ",".join(attached),
        }
    )

    # Update groups with users
    for g in assigned_groups:
        idx = groups_df[groups_df["GroupId"] == g].index[0]
        existing = groups_df.at[idx, "Users"]
        groups_df.at[idx, "Users"] = existing + "," + uid if existing else uid

users_df = pd.DataFrame(users)

# ------------------------
# GENERATE ROLES
# ------------------------
roles = []

for i in range(NUM_ROLES):
    rid = random_id("role-")

    attached = random.sample(policy_ids, random.randint(1, 8))

    roles.append(
        {
            "RoleName": f"Role_{i}",
            "RoleId": rid,
            "Arn": f"arn:aws:iam::role/{rid}",
            "AttachedPolicies": ",".join(attached),
        }
    )

roles_df = pd.DataFrame(roles)

# ------------------------
# SAVE FILES
# ------------------------
policies_df.to_csv("./policies.csv", index=False)
users_df.to_csv("./users.csv", index=False)
groups_df.to_csv("./groups.csv", index=False)
roles_df.to_csv("./roles.csv", index=False)

print("Synthetic dataset generated successfully.")
