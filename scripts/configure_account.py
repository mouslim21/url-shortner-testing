"""Prepare account inputs without provisioning or copying credentials."""

import argparse
import ipaddress
import json
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "mouslim21/url-shortner-testing"


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        # CLI output is deliberately not replayed: credentials must stay private.
        raise RuntimeError(f"{args[0]} {args[1]} failed; check authentication/access")
    return result.stdout


def aws_json(profile: str, region: str, *args: str) -> dict:
    return json.loads(
        command(
            [
                "aws",
                *args,
                "--profile",
                profile,
                "--region",
                region,
                "--output",
                "json",
                "--no-cli-pager",
            ]
        )
    )


def administrator(identity: dict, profile: str, region: str, explicit: str) -> str:
    account = identity["Account"]
    arn = explicit or identity["Arn"]
    if not explicit and ":assumed-role/" in arn:
        role_name = arn.split(":assumed-role/", 1)[1].split("/", 1)[0]
        arn = aws_json(profile, region, "iam", "get-role", "--role-name", role_name)[
            "Role"
        ]["Arn"]
    if not re.fullmatch(rf"arn:aws:iam::{account}:(user|role)/.+", arn):
        raise ValueError("Use a stable IAM user/role ARN in the selected account")
    if arn.endswith(":role/url-shortner-github-actions"):
        raise ValueError("The publishing role cannot administer the cluster")
    return arn


def render_files(
    account: str,
    region: str,
    admin: str,
    profile: str,
    directory: Path,
    cidrs: list[str],
    alert_email: str,
) -> dict[str, str]:
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Invalid AWS account ID")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region):
        raise ValueError("Invalid AWS region")
    for cidr in cidrs:
        if ipaddress.ip_network(cidr).version != 4:
            raise ValueError("Public API CIDRs must use IPv4")
    bucket = f"url-shortner-tfstate-{account}-{region}"
    shared = {"aws_account_id": account, "aws_region": region}
    bootstrap = {**shared, "state_bucket_name": bucket}
    nonprod = {
        **shared,
        "eks_admin_principal_arn": admin,
        "eks_kubernetes_version": "1.35",
        "eks_public_endpoint_enabled": bool(cidrs),
        "eks_trusted_public_cidrs": cidrs,
        "alert_email": alert_email,
    }
    # Values use JSON string escaping, which is also valid in HCL assignments.
    backend = f"bucket = {json.dumps(bucket)}\nregion = {json.dumps(region)}\n"
    backend += f"allowed_account_ids = {json.dumps([account])}\n"
    local_backend = f"path = {json.dumps(str(directory / 'bootstrap.tfstate'))}\n"
    environment = {
        "AWS_PROFILE": profile,
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
        "AWS_ACCOUNT_ID": account,
        "ACCOUNT_CONFIG_DIR": str(directory),
    }
    env = "".join(
        f"export {key}={shlex.quote(value)}\n" for key, value in environment.items()
    )
    for name, root in [("bootstrap", "bootstrap"), ("nonprod", "environments/nonprod")]:
        tf_root = shlex.quote(str(ROOT / "infrastructure/terraform" / root))
        env += (
            f'tf_{name}() {{ TF_DATA_DIR="$ACCOUNT_CONFIG_DIR/{name}-data" '
            f'terraform -chdir={tf_root} "$@"; }}\n'
        )
    return {
        "bootstrap.tfvars.json": json.dumps(bootstrap, indent=2) + "\n",
        "nonprod.tfvars.json": json.dumps(nonprod, indent=2) + "\n",
        "backend.hcl": backend,
        "bootstrap-backend.hcl": local_backend,
        "env.sh": env,
    }


def write_files(directory: Path, files: dict[str, str]) -> None:
    # Preserve edits such as alert destinations and API settings on repeat runs.
    conflicts = [
        name
        for name, text in files.items()
        if (directory / name).exists() and (directory / name).read_text() != text
    ]
    if conflicts:
        raise ValueError(
            "Existing configuration preserved; review files: " + ", ".join(conflicts)
        )
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text)


def sync_github(account: str, region: str, profile: str) -> None:
    # Require provisioning to have finished before changing the pipeline target.
    publishing = aws_json(
        profile, region, "iam", "get-role", "--role-name", "url-shortner-github-actions"
    )["Role"]["Arn"]
    deployment = aws_json(
        profile,
        region,
        "iam",
        "get-role",
        "--role-name",
        "url-shortner-github-deploy-nonprod",
    )["Role"]["Arn"]
    cluster = aws_json(
        profile, region, "eks", "describe-cluster", "--name", "url-shortner-nonprod-eks"
    )["cluster"]
    if cluster["status"] != "ACTIVE":
        raise ValueError("Provision an ACTIVE cluster before syncing GitHub")
    for arn in [publishing, deployment, cluster["arn"]]:
        if arn.split(":")[4] != account:
            raise ValueError("Target resource belongs to a different account")
    policies = json.loads(
        command(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/environments/nonprod/deployment-branch-policies",
            ]
        )
    )["branch_policies"]
    if [(p["name"], p["type"]) for p in policies] != [("main", "branch")]:
        raise ValueError("Configure the nonprod environment to allow only main first")
    values = {
        "AWS_ACCOUNT_ID": account,
        "AWS_REGION": region,
        "EKS_CLUSTER_NAME": cluster["name"],
        "AWS_ROLE_ARN": publishing,
    }
    for name, value in values.items():
        command(["gh", "variable", "set", name, "--repo", REPOSITORY, "--body", value])
    command(
        [
            "gh",
            "variable",
            "set",
            "AWS_DEPLOY_ROLE_ARN",
            "--repo",
            REPOSITORY,
            "--env",
            "nonprod",
            "--body",
            deployment,
        ]
    )
    print("Updated testing repository variables; no workflow was triggered.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--admin-principal-arn", default="")
    parser.add_argument("--public-api-cidr", action="append", default=[])
    parser.add_argument("--alert-email", default="")
    parser.add_argument(
        "--sync-github",
        action="store_true",
        help="Update testing repo variables after infrastructure exists",
    )
    args = parser.parse_args()
    identity = aws_json(args.profile, args.region, "sts", "get-caller-identity")
    account = identity["Account"]
    admin = administrator(identity, args.profile, args.region, args.admin_principal_arn)
    directory = ROOT / ".local/accounts" / account / args.region
    files = render_files(
        account,
        args.region,
        admin,
        args.profile,
        directory,
        args.public_api_cidr,
        args.alert_email,
    )
    write_files(directory, files)
    print(f"Prepared account {account} in {args.region}; administrator {admin}")
    print(f"source {shlex.quote(str(directory / 'env.sh'))}")
    print(
        "tf_bootstrap init -reconfigure "
        '-backend-config="$ACCOUNT_CONFIG_DIR/bootstrap-backend.hcl"'
    )
    print(
        'tf_bootstrap plan -var-file="$ACCOUNT_CONFIG_DIR/bootstrap.tfvars.json" '
        '-out="$ACCOUNT_CONFIG_DIR/bootstrap.tfplan"'
    )
    print("Review and approve bootstrap before applying; then initialize nonprod:")
    print(
        'tf_nonprod init -reconfigure -backend-config="$ACCOUNT_CONFIG_DIR/backend.hcl"'
    )
    print(
        'tf_nonprod plan -var-file="$ACCOUNT_CONFIG_DIR/nonprod.tfvars.json" '
        '-out="$ACCOUNT_CONFIG_DIR/nonprod.tfplan"'
    )
    if args.sync_github:
        sync_github(account, args.region, args.profile)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError) as exc:
        raise SystemExit(str(exc)) from None
