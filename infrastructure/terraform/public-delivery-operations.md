# CI/CD delivery from the testing repository

Repository: `mouslim21/url-shortner-testing`. Account: the selected `AWS_ACCOUNT_ID`.
Region: the selected `AWS_REGION`. Original source baseline: `ee8ad08f2bce7b1d0f3f868d9cf136aabbb646f0`.
All development and GitHub settings changes belong to this testing repository.

## Pipeline behavior

- PRs targeting `main` run Compose validation, Ruff, Pytest, Docker builds and
  Trivy scans. They cannot publish images or deploy.
- Merging a PR into `main` creates a `push` event on `main`, which runs validation,
  publishes scanned images with the exact commit SHA and deploys nonprod.
- Closing a PR without merging does not deploy.
- Feature-branch pushes do not trigger this workflow. There is no manual CI
  dispatch trigger or branch publishing/deployment option.
- The separate manual OIDC identity test accepts `main` only and never deploys.
- Build artifacts are uploaded only on successful `main` push delivery runs.
  Publishing loads those exact scanned archives and verifies immutable image IDs.
- Deployment uses a GitHub-hosted runner and OIDC. The sandbox EKS API is public
  for `0.0.0.0/0` by explicit user request; private access also remains enabled.
  API reachability does not grant Kubernetes authorization.
- The application deployer stays namespace-scoped. It cannot install controllers,
  change cluster RBAC, create namespaces, or manage `kube-system`.

See [account-setup.md](account-setup.md) for generated account inputs, partial backend initialization, and GitHub variable synchronization.

## GitHub configuration

Repository variables:

| Variable | Value |
| --- | --- |
| `AWS_ACCOUNT_ID` | `<ACCOUNT_ID>` |
| `AWS_REGION` | Selected deployment region |
| `EKS_CLUSTER_NAME` | `url-shortner-nonprod-eks` |
| `AWS_ROLE_ARN` | `arn:aws:iam::<ACCOUNT_ID>:role/url-shortner-github-actions` |

Environment `nonprod`:

- Custom branch policies permit `main` only, with no tag policies.
- `AWS_DEPLOY_ROLE_ARN` = `arn:aws:iam::<ACCOUNT_ID>:role/url-shortner-github-deploy-nonprod`.
- No static AWS access keys are stored in GitHub.

GitHub confirmed immutable OIDC subject prefix:
`repo:mouslim21@55384360/url-shortner-testing@1444289754`.
Publishing trusts only its exact `ref:refs/heads/main`
subject; deployment trusts its exact `environment:nonprod` subject. Both require
`sts.amazonaws.com` audience. Terraform must apply the reviewed trust changes
before either role can authenticate this repository. GitHub variables alone do
not update AWS trust.

## One-time administrator bootstrap

This prepares infrastructure prerequisites, not a manual application deployment.
No new administrative permissions are added to the application deployer.
The Terraform trust plan and creation of controller/Kubernetes resources require
explicit user approval before executing these operations.

From this repository root with the administrator profile:

```bash
export AWS_PROFILE=kata-dev AWS_REGION=us-east-1
export KUBECONFIG=/tmp/url-shortner-testing-admin.kubeconfig
aws eks update-kubeconfig --name url-shortner-nonprod-eks
# Review a fresh Terraform plan, then apply it only after approval.
terraform -chdir=infrastructure/terraform/environments/nonprod plan -out=ci-trust.tfplan
# After approval only:
# terraform -chdir=infrastructure/terraform/environments/nonprod apply ci-trust.tfplan
kubectl apply -f infrastructure/kubernetes/nonprod-deployer-rbac.yaml
kubectl -n kube-system rollout status daemonset/eks-pod-identity-agent --timeout=180s
helm repo add eks https://aws.github.io/eks-charts
helm repo update eks
VPC_ID=$(aws eks describe-cluster --name url-shortner-nonprod-eks --query cluster.resourcesVpcConfig.vpcId --output text)
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  --version 3.6.2 --namespace kube-system \
  -f infrastructure/kubernetes/aws-load-balancer-controller-values.yaml \
  --set-string vpcId="$VPC_ID" --set-string region="$AWS_REGION" --atomic --wait --timeout 10m
kubectl -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=180s
```

Controller chart 3.6.2 matches the vendored IAM policy. Its existing Pod Identity
association uses `kube-system/aws-load-balancer-controller`. The controller later
creates the application's internet-facing ALB and related AWS resources; these
incur charges. The application is configured for HTTP until a domain and TLS
certificate are supplied.

Local bootstrap/nonprod `terraform.tfvars` files contain account-specific inputs
and remain ignored. Preserve Terraform state and review all plans. The nonprod
configuration enables public API access in local inputs; the source module still
defaults to private-only API access.

## Deploy through a PR merge

Push the feature branch to this testing repository, then review and merge its PR
into `main` when authorized. Do not push changes to the original repository.
This change does not create or merge a PR automatically.

```bash
gh run list --repo mouslim21/url-shortner-testing --workflow ci.yml --branch main --limit 5
```

CI must succeed through lint/tests, all three builds and scan gates, OIDC/ECR
publishing, and Helm deployment. Its output includes the deployed commit and
Application URL. Verify frontend access, URL creation, 302 destination and the
analytics counter through that URL. SNS email delivery remains unconfigured
until an alert email is supplied and its subscription confirmed.

The workflow runs on any `main` push, including direct pushes if repository rules
permit them. Enforce a required-PR branch protection rule separately if direct
pushes to `main` must be prohibited.
