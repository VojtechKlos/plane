# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""A work item attachment downloads by default. `?disposition=inline` previews it in the
browser, but only for an allow-listed image type, whose Content-Type is then pinned; any
other type is still served as an attachment."""

from unittest import mock
from uuid import uuid4

import pytest

from plane.db.models import FileAsset, Issue, Project, ProjectMember

ATTACHMENT_URL = "/api/assets/v2/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/attachments/{pk}/"


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize(
    "claimed_type, query, expected_disposition, expected_type",
    [
        ("image/png", "?disposition=inline", "inline", "image/png"),
        ("image/svg+xml", "?disposition=inline", "attachment", None),
        ("text/html", "?disposition=inline", "attachment", None),
        ("image/png", "", "attachment", None),
        ("image/png", "?disposition=bogus", "attachment", None),
    ],
)
def test_issue_attachment_disposition_follows_allow_list(
    session_client, workspace, create_user, claimed_type, query, expected_disposition, expected_type
):
    project = Project.objects.create(name="Attachments", identifier="ATT", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    issue = Issue.objects.create(name="With attachment", project=project, workspace=workspace)
    asset = FileAsset.objects.create(
        attributes={"name": "a.png", "type": claimed_type, "size": 10},
        asset=f"{workspace.id}/{uuid4().hex}-a.png",
        size=10,
        workspace=workspace,
        project=project,
        issue_id=issue.id,
        created_by=create_user,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        is_uploaded=True,
    )
    url = ATTACHMENT_URL.format(slug=workspace.slug, project_id=project.id, issue_id=issue.id, pk=asset.id)

    with mock.patch("plane.settings.storage.boto3") as mock_boto3:
        s3_client = mock_boto3.client.return_value
        s3_client.generate_presigned_url.return_value = "https://signed.example/attachment"
        response = session_client.get(f"{url}{query}")

    assert response.status_code == 302
    params = s3_client.generate_presigned_url.call_args.kwargs["Params"]
    assert params["ResponseContentDisposition"].split(";")[0] == expected_disposition
    assert params.get("ResponseContentType") == expected_type
    assert "a.png" in params["ResponseContentDisposition"]
