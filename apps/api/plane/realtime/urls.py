# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from .views import RealtimeProjectsEndpoint, WorkItemDescriptionEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/realtime/projects/",
        RealtimeProjectsEndpoint.as_view(),
        name="realtime-projects",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:pk>/realtime-description/",
        WorkItemDescriptionEndpoint.as_view(),
        name="work-item-realtime-description",
    ),
]
