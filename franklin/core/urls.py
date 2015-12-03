from django.conf.urls import url

from .views import ConvertTokenView, health
from builder.views import UpdateBuildStatus
from github.views import deploy_hook, deployed_repos, deployable_repos, \
    register_repo, get_auth_token

urlpatterns = [
    url(r'^auth/github/$', get_auth_token, name='get_token'),
    url(r'^deployed/$', deploy_hook, name='deploy'),
    url(r'^register/$', register_repo, name='register'),
    url(r'^user/repos/deployed/$', deployed_repos, name='deployed_repos'),
    url(r'^user/repos/deployable/$', deployable_repos, name='deployable_repos'),
    url(r'^health/$', health, name='health'),
    url(r'^auth/convert-token/?$', ConvertTokenView.as_view(),
        name="convert_token"),
    url(r'^build/(?P<pk>\d+)/update/$', UpdateBuildStatus.as_view(),
        name='build'),
]
