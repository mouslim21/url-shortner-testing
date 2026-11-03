import json
from unittest.mock import Mock

import pytest

from scripts import configure_account as setup


@pytest.mark.parametrize(
    "account,region", [("111122223333", "us-east-1"), ("444455556666", "eu-west-1")]
)
def test_account_inputs_and_state_are_separate(tmp_path, account, region):
    directory = tmp_path / account / region
    arn = f"arn:aws:iam::{account}:user/test"
    files = setup.render_files(account, region, arn, "test", directory, [], "")
    inputs = json.loads(files["nonprod.tfvars.json"])
    assert inputs["aws_account_id"] == account
    assert inputs["aws_region"] == region
    assert inputs["eks_admin_principal_arn"] == arn
    assert inputs["eks_public_endpoint_enabled"] is False
    assert f"url-shortner-tfstate-{account}-{region}" in files["backend.hcl"]
    assert str(directory / "bootstrap.tfstate") in files["bootstrap-backend.hcl"]
    assert "TF_DATA_DIR=" in files["env.sh"]
    assert "AWS_ACCESS_KEY_ID" not in "".join(files.values())
    assert "AWS_SECRET_ACCESS_KEY" not in "".join(files.values())


def test_setup_preserves_existing_configuration(tmp_path):
    setup.write_files(tmp_path, {"inputs.json": '{"alert_email":"keep"}'})
    with pytest.raises(ValueError, match="preserved"):
        setup.write_files(tmp_path, {"inputs.json": '{"alert_email":""}'})
    assert json.loads((tmp_path / "inputs.json").read_text())["alert_email"] == "keep"


def test_setup_is_idempotent(tmp_path):
    setup.write_files(tmp_path, {"inputs.json": "{}"})
    setup.write_files(tmp_path, {"inputs.json": "{}"})
    assert (tmp_path / "inputs.json").read_text() == "{}"


def test_assumed_role_resolves_stable_arn_with_path(monkeypatch):
    identity = {
        "Account": "111122223333",
        "Arn": "arn:aws:sts::111122223333:assumed-role/Admin/session",
    }
    stable = "arn:aws:iam::111122223333:role/team/Admin"
    monkeypatch.setattr(setup, "aws_json", Mock(return_value={"Role": {"Arn": stable}}))
    assert setup.administrator(identity, "test", "us-east-1", "") == stable


def test_administrator_from_another_account_is_rejected():
    with pytest.raises(ValueError, match="selected account"):
        setup.administrator(
            {"Account": "111122223333"},
            "test",
            "us-east-1",
            "arn:aws:iam::444455556666:user/test",
        )


def test_sync_fails_before_github_changes_when_roles_missing(monkeypatch):
    monkeypatch.setattr(setup, "aws_json", Mock(side_effect=RuntimeError("no role")))
    gh = Mock()
    monkeypatch.setattr(setup, "command", gh)
    with pytest.raises(RuntimeError, match="no role"):
        setup.sync_github("111122223333", "us-east-1", "test")
    gh.assert_not_called()


def test_sync_targets_selected_account_without_workflow_dispatch(monkeypatch):
    account = "444455556666"
    monkeypatch.setattr(
        setup,
        "aws_json",
        Mock(
            side_effect=[
                {"Role": {"Arn": f"arn:aws:iam::{account}:role/publisher"}},
                {"Role": {"Arn": f"arn:aws:iam::{account}:role/deployer"}},
                {
                    "cluster": {
                        "status": "ACTIVE",
                        "name": "url-shortner-nonprod-eks",
                        "arn": f"arn:aws:eks:eu-west-1:{account}:cluster/test",
                    }
                },
            ]
        ),
    )
    gh = Mock(
        side_effect=[
            json.dumps({"branch_policies": [{"name": "main", "type": "branch"}]}),
            "",
            "",
            "",
            "",
            "",
        ]
    )
    monkeypatch.setattr(setup, "command", gh)
    setup.sync_github(account, "eu-west-1", "test")
    calls = [call.args[0] for call in gh.call_args_list]
    assert any("AWS_ACCOUNT_ID" in c and account in c for c in calls)
    assert any(
        "AWS_DEPLOY_ROLE_ARN" in c and f"arn:aws:iam::{account}:role/deployer" in c
        for c in calls
    )
    assert all("workflow" not in c for c in calls)
