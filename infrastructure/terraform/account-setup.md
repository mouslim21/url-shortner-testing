# Reuse this repository with another AWS account

No application, workflow, or Terraform source edits are needed to change AWS
accounts. Configure credentials through your AWS CLI profile; never paste them
into repository files or GitHub secrets. This workflow supports standard AWS
commercial accounts. The GitHub repository and its immutable OIDC subject stay
the same when the AWS account changes.

## 1. Generate local account configuration

From the repository root:

```bash
python3 scripts/configure_account.py --profile kata-dev --region us-east-1
```

The command reads STS identity, resolves the stable administrator IAM ARN, and
writes ignored configuration under `.local/accounts/<ACCOUNT_ID>/<REGION>/`:

- `bootstrap.tfvars.json`: expected account, region and unique state bucket name.
- `nonprod.tfvars.json`: expected account, region and administrator ARN.
- `backend.hcl`: that account's S3 bucket, region and backend account guard.
- `bootstrap-backend.hcl`: that account's separate local bootstrap state path.
- `env.sh`: profile selection and `tf_bootstrap` / `tf_nonprod` shell functions,
  each using separate backend metadata per account and root module.

No credentials are copied or displayed. No resources, Terraform operations or
GitHub changes occur by default. Existing generated files with differing values
are preserved and reported rather than overwritten. Account/region values and
paths come from the selected CLI profile and command arguments.

The EKS API defaults to private-only. To explicitly request public administration,
add `--public-api-cidr YOUR_IP/32`. The previously requested all-IPv4 mode is
`--public-api-cidr 0.0.0.0/0`; IAM authentication remains required. Use
`--admin-principal-arn arn:aws:iam::<ACCOUNT_ID>:role/path/RoleName` when automatic
assumed-role resolution is unavailable. Optional `--alert-email ADDRESS` configures
an SNS email subscription, which still requires email confirmation after apply.
Repeat the same options when repeating setup or syncing GitHub.

## 2. Bootstrap and provision after reviewing plans

Source the `env.sh` path printed by setup. Then:

```bash
tf_bootstrap init -reconfigure -backend-config="$ACCOUNT_CONFIG_DIR/bootstrap-backend.hcl"
tf_bootstrap plan -var-file="$ACCOUNT_CONFIG_DIR/bootstrap.tfvars.json" -out="$ACCOUNT_CONFIG_DIR/bootstrap.tfplan"
# After plan review and explicit approval:
tf_bootstrap apply "$ACCOUNT_CONFIG_DIR/bootstrap.tfplan"

tf_nonprod init -reconfigure -backend-config="$ACCOUNT_CONFIG_DIR/backend.hcl"
tf_nonprod plan -var-file="$ACCOUNT_CONFIG_DIR/nonprod.tfvars.json" -out="$ACCOUNT_CONFIG_DIR/nonprod.tfplan"
# After plan review and explicit approval:
tf_nonprod apply "$ACCOUNT_CONFIG_DIR/nonprod.tfplan"
```

The helper prints these init/plan commands but never runs them. Preserve bootstrap
state and local account configuration; the new S3 bucket does not store bootstrap
state automatically. Existing installations must retain their current state:
copy/back up the old bootstrap state to the generated bootstrap path, or use a
reviewed Terraform state migration. Never initialize an empty replacement state
and apply against already-created infrastructure. Existing nonprod state retains
its `nonprod/terraform.tfstate` key in the account's bucket.

Use `-reconfigure` with the generated backend file when switching accounts; do not
migrate another account's state into the new account. The generated `TF_DATA_DIR`
locations isolate cached backend settings. Explicit `-var-file` inputs override
any older automatically loaded local `.tfvars` values. Review old local files for
other custom settings you want to retain. Do not reuse old saved plans.

## 3. Point GitHub at the provisioned account

After the cluster and both roles exist, repeat setup with the same options plus:

```bash
python3 scripts/configure_account.py --profile kata-dev --region us-east-1 --sync-github
```

This verifies roles belong to the selected account and the cluster is ACTIVE,
then updates these variables only in `mouslim21/url-shortner-testing`:

- Repository: `AWS_ACCOUNT_ID`, `AWS_REGION`, `EKS_CLUSTER_NAME`, `AWS_ROLE_ARN`.
- Environment `nonprod`: `AWS_DEPLOY_ROLE_ARN`.

The `nonprod` environment must already permit only branch `main`; setup refuses
other environment policies. It never grants feature-branch delivery access or
triggers a workflow. An interrupted variable sync can be rerun; avoid merging
while switching the target account. No GitHub settings are changed without the
explicit `--sync-github` option.

## 4. Bootstrap cluster prerequisites, then merge a PR

Follow [public-delivery-operations.md](public-delivery-operations.md) to install
namespace/RBAC and the ALB controller using the existing Terraform-created IAM
prerequisites. Supply its `region` from `$AWS_REGION`, cluster name from Terraform,
and VPC ID from AWS/Terraform. Provisioning controllers and public load balancers
requires approval and may incur charges.

Application delivery stays exclusively on CI runs for `main`, normally after a PR
merge. Once these portable changes have been merged, a new account can use a
rerun of an existing successful `main` CI run after provisioning and variable
synchronization; no source change or feature-branch deployment is needed. Select
a run whose workflow already contains the portable configuration and whose branch
is `main`. Alternatively, the next normal PR merge starts a new main run. PRs validate only; closing a PR without merging does not deploy. CI reads
required GitHub account/region variables, uses OIDC to authenticate, passes the
actual ECR login registry to Helm, and overrides the chart's region. Helm refuses
to deploy without a registry; image publishing refuses missing account/region
configuration. No account-specific fallbacks exist in executable configuration.

Static account examples in `*.example` files are illustrations; the helper creates
the real ignored inputs. A new account still needs resources provisioned and its
cluster bootstrapped; changing variables alone cannot deploy absent infrastructure.
