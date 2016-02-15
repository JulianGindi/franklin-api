import logging
import os
import yaml

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .serializers import GithubWebhookSerializer
from builder.models import Site
from builder.serializers import SiteSerializer
from core.helpers import make_rest_get_call, make_rest_post_call, do_auth,\
                         GithubOnly
from users.serializers import UserSerializer


github_secret = os.environ['SOCIAL_AUTH_GITHUB_SECRET']
base_url = os.environ['API_BASE_URL']
github_base = 'https://api.github.com/'

logger = logging.getLogger(__name__)


def get_franklin_config(site, user):
    url = github_base + 'repos/' + site.owner.name + '/'\
            + site.name + '/contents/.franklin.yml'
    # TODO - This will fetch the file from the default master branch
    social = user.social_auth.get(provider='github')
    token = social.extra_data['access_token']
    headers = {
                'content-type': 'application/json',
                'Authorization': 'token ' + token
              }
    config_metadata = make_rest_get_call(url, headers)

    if status.is_success(config_metadata.status_code):
        download_url = config_metadata.json().get('download_url', None)
        config_payload = make_rest_get_call(download_url, None)
        if status.is_success(config_payload.status_code):
            # TODO - validation and cleanup needed here similar to:
            # http://stackoverflow.com/a/22231372
            franklin_config = yaml.load(config_payload.text)
            return franklin_config
        else:
            return config_payload
    return config_metadata


def create_repo_webhook(site, user):
    # TODO - check for existing webhook and update if needed (or skip)
    social = user.social_auth.get(provider='github')
    token = social.extra_data['access_token']

    # TODO - Confirm that a header token is the best/most secure way to go
    headers = {
                'content-type': 'application/json',
                'Authorization': 'token ' + token
              }
    body = {
                'name': 'web',
                'events': ['push'],
                'active': True,
                'config': {
                                'url': base_url + 'deployed/',
                                'content_type': 'json',
                                'secret': os.environ['GITHUB_SECRET']
                          }
            }
    url = github_base + 'repos/' + site.owner.name + '/' + site.name + '/hooks'
    return make_rest_post_call(url, headers, body)


def create_repo_deploy_key(site, user):
    # TODO - check for existing and update if needed (or skip)
    social = user.social_auth.get(provider='github')
    token = social.extra_data['access_token']

    headers = {
                'content-type': 'application/json',
                'Authorization': 'token ' + token
              }
    body = {
                'title': 'franklin readonly deploy key',
                'key': site.deploy_key,
                'read_only': True
            }
    url = github_base + 'repos/' + site.owner.name + '/' + site.name + '/keys'
    return make_rest_post_call(url, headers, body)


@api_view(['GET', 'POST'])
def repository_list(request):
    """
    Get all repos currently deployed by Franklin that the user can manage or
    register a new repo
    ---

    type:
        200:
            type: string
            description: Successful Update
        201:
            type: string
            description: Successful Creation

    response_serializer: SiteSerializer
    request_serializer: SiteSerializer
    omit_serializer: false

    parameters_strategy:
        form: merge
    parameters:
        - name: name
          type: string
          required: true
        - name: github_id
          type: integer
          required: true
        - name: owner
          pytype: builder.serializers.OwnerSerializer
          required: true
    responseMessages:
        - code: 400
          message: Invalid json received or Bad Request from Github
        - code: 403
          message: Current user does not have permission for this repo
        - code: 422
          message: Validation error from Github
        - code: 500
          message: Error from Github.
    """

    if request.method == 'GET':
        if request.user.details.sites.count() == 0:
            github_repos = request.user.details.get_user_repos()
            # TODO - return error from github
            request.user.details.update_repos_for_user(github_repos)
        sites = request.user.details.sites.all()
        serializer = SiteSerializer(sites, many=True)
        return Response(serializer.data)
    elif request.method == 'POST':
        serializer = SiteSerializer(data=request.data)
        if serializer and serializer.is_valid():
            site = serializer.save()
            if not request.user.details.has_repo_access(site):
                message = 'Current user does not have permission for this repo'
                logger.warn(message + ' | %s | %s', request.user, site)
                return Response(message, status=status.HTTP_403_FORBIDDEN)
        config = get_franklin_config(site, request.user)
        if config and not hasattr(config, 'status_code'):
            # Optional. Update DB with any relevant .franklin config
            pass
        webhook_response = create_repo_webhook(site, request.user)
        if not status.is_success(webhook_response.status_code):
            return Response(status=webhook_response.status_code)
        deploy_key_response = create_repo_deploy_key(site, request.user)
        if not status.is_success(deploy_key_response.status_code):
            return Response(status=deploy_key_response.status_code)
        return Response(status=status.HTTP_201_CREATED)
    return Response(status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'DELETE'])
def repository_detail(request, pk):
    """
    Rerieve or Delete a Github project with franklin
    ---

    type:
        204:
            type: string
            decription: Successful Deletion

    parameters:
        - name: github_id
          type: integer
          description: The github id for the repo, passed in the URL
          required: true
    responseMessages:
        - code: 400
          message: Invalid json received or Bad Request from Github
        - code: 403
          message: Current user does not have permission for this repo
        - code: 422
          message: Validation error from Github
    """

    try:
        site = Site.objects.get(github_id=pk)
    except Site.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)
    if not request.user.details.has_repo_access(site):
        message = 'Current user does not have permission for this repo'
        logger.warn(message + ' | %s | %s', request.user, site)
        return Response(message, status=status.HTTP_403_FORBIDDEN)
    if request.method == 'GET':
        serializer = SiteSerializer(site)
        return Response(serializer.data)
    elif request.method == 'DELETE':
        site.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
def deployable_repos(request):
    """
    All repos from Github that the user has the permission level to deploy
    ---

    responseMessages:
        - code: 500
          message: Error from Github.
    """
    if request.method == 'GET':
        # TODO - in the model, github response should map to a serializer which
        # we should use here to define the respone type
        github_repos = request.user.details.get_user_repos()
        # TODO - return error from github

        # While we are here, might as well update linkages between the user and
        # all active repos managed by Franklin
        request.user.details.update_repos_for_user(github_repos)
        return Response(github_repos, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes((GithubOnly, ))
def deploy_hook(request):
    """
    Private endpoint that should only be called from Github
    ---

    type:
        200:
            type: string
        201:
            type: string
        204:
            type: string

    request_serializer: GithubWebhookSerializer
    omit_serializer: false

    parameters_strategy:
        form: merge
    parameters:
        - name: head_commit
          pytype: github.serializers.HeadCommitSerializer
          required: true
        - name: repository
          pytype: github.serializers.RepositorySerializer
          required: true
    responseMessages:
        - code: 400
          message: Invalid json received or something else wrong.
    """
    if request.method == 'POST':
        event_type = request.META.get("HTTP_X_GITHUB_EVENT")
        if event_type:
            if event_type in ['push', 'create']:
                github_event = GithubWebhookSerializer(data=request.data)
                if github_event and github_event.is_valid():
                    site = github_event.get_existing_site()
                    if site:
                        environment = site.get_deployable_event(github_event)
                        if environment:
                            # This line helps with testing.
                            # We will remove once we add mocking.
                            if os.environ['ENV'] is not 'test':
                                environment.build()
                                return Response(status=status.HTTP_201_CREATED)
                        else:
                            # Likely a webhook we don't build for.
                            return Response(status=status.HTTP_200_OK)
                else:
                    logger.warning("Received invalid Github Webhook message")
            elif event_type == 'ping':
                # TODO - update the DB with some important info here
                # repository{
                #           id, name,
                #           owner{ id, login },
                #           sender{ id, login, site_admin }
                #           }
                return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            logger.warning("Received a malformed POST message")
    else:
        # Invalid methods are caught at a higher level
        pass
    return Response(status=status.HTTP_400_BAD_REQUEST)


def get_access_token(request):
    """
    Tries to get the access token from an OAuth Provider
    :param request:
    :param backend:
    :return:
    """
    url = 'https://github.com/login/oauth/access_token'
    secret = github_secret

    headers = {
        'content-type': 'application/json',
        'accept': 'application/json'
    }
    params = {
        "code": request.data.get('code'),
        "client_id": request.data.get('clientId'),
        "redirect_uri": request.data.get('redirectUri'),
        "client_secret": secret
    }

    # Exchange authorization code for access token.
    r = make_rest_post_call(url, headers, params)
    if status.is_success(r.status_code):
        try:
            access_token = r.json().get('access_token', None)
            user = do_auth(access_token)
            serializer = UserSerializer(user)
            response_data = Response({
                'token': access_token,
                'user': serializer.data
            }, status=status.HTTP_200_OK)
        except KeyError:
            response_data = Response({'status': 'Bad request',
                                      'message': 'Authentication could not be\
                                              performed with received data.'},
                                     status=status.HTTP_400_BAD_REQUEST)
        return response_data
    else:
        return Response(status=r.status_code)


@api_view(['POST'])
@permission_classes((AllowAny, ))
def get_auth_token(request):
    """
    View to authenticate with github using a client code
    ---

    type:
        token:
            type: string
            required: true
            description: oAuth token for the github user
    parameters:
        -   name: clientId
            type: string
            required: true
        -   name: redirectUri
            type: string
            required: true
        -   name: code
            type: string
            required: true
    """

    logger.info("Received token request from Dashboard")

    return get_access_token(request)
