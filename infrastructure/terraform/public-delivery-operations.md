# CI/CD delivery from the testing repository

Repository: `mouslim21/url-shortner-testing`. Account: `478728046113`.
Region: `us-east-1`. Original source baseline: `ee8ad08f2bce7b1d0f3f868d9cf136aabbb646f0`.
All development and GitHub settings changes belong to this testing repository.

## Pipeline behavior

- Pushes to `main` and `feature/debug`, and PRs targeting `main`, run Compose
  validation, Ruff, Pytest, Docker builds, and Trivy scanning.
- Successful `main` pushes publish validated images and deploy automatically.
- `feature/debug` pushes validate without publishing or deploying. To deploy this
  branch, explicitly run workflow `CI` with input `deploy=true` and that branch.
- Manual runs with `deploy=false` perform validation only.
- Build artifacts are uploaded for delivery runs. Publishing loads those exact
  scanned image archives and verifies immutable full-commit-SHA image identity.
- Deployment uses a GitHub-hosted runner and OIDC. The sandbox EKS API is public
  for `0.0.0.0/0` by explicit user request; private access also remains enabled.
  API reachability does not grant Kubernetes authorization.
- The application deployer stays namespace-scoped. It cannot install controllers,
  change cluster RBAC, create namespaces, or manage `kube-system`.

## GitHub configuration

Repository variables:

| Variable | Value |
| --- | --- |
| `AWS_ACCOUNT_ID` | `478728046113` |
| `AWS_REGION` | `us-east-1` |
| `EKS_CLUSTER_NAME` | `url-shortner-nonprod-eks` |
| `AWS_ROLE_ARN` | `arn:aws:iam::478728046113:role/url-shortner-github-actions` |

Environment `nonprod`:

- Custom branch policies permit `main` and `feature/debug` only, with no tag policies.
- `AWS_DEPLOY_ROLE_ARN` = `arn:aws:iam::478728046113:role/url-shortner-github-deploy-nonprod`.
- No static AWS access keys are stored in GitHub.

GitHub confirmed immutable OIDC subject prefix:
`repo:mouslim21@55384360/url-shortner-testing@1444289754`.
Publishing trusts its exact `ref:refs/heads/main` and `ref:refs/heads/feature/debug`
subjects; deployment trusts its exact `environment:nonprod` subject. Both require
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
  --set-string vpcId="$VPC_ID" --atomic --wait --timeout 10m
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

## Run the application pipeline after prerequisites and approval

```bash
gh workflow run ci.yml --repo mouslim21/url-shortner-testing --ref feature/debug -f deploy=true
gh run list --repo mouslim21/url-shortner-testing --workflow ci.yml --limit 5
```

The workflow must succeed through lint/tests, all three builds and scan gates,
OIDC/ECR publishing, and Helm deployment. Its final output includes the deployed
commit and Application URL. Do not equate validation success with delivery.
Verify frontend access, URL creation, 302 destination, and analytics counter
through the public URL. SNS email delivery remains unconfigured until an alert
email is supplied and its subscription confirmed.
