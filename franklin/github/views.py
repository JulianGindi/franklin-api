import logging
import os

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .api import create_repo_deploy_key, create_repo_webhook, \
        delete_deploy_key, delete_webhook, get_access_token, \
        get_franklin_config
from .serializers import GithubWebhookSerializer
from builder.models import Site
from builder.serializers import SiteSerializer
from core.helpers import GithubOnly


logger = logging.getLogger(__name__)


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
        sites = request.user.details.sites.filter(is_active=True).all()
        serializer = SiteSerializer(sites, many=True)
        return Response(serializer.data)
    elif request.method == 'POST':
        # TODO - #53 will have us refactoring all errors returned from our
        # external dependencies. It will need to handle cases like this where
        # we get a potential non-error like this one.
        serializer = SiteSerializer(data=request.data)
        if serializer and serializer.is_valid():
            try:
                site = Site.objects.get(github_id=request.data['github_id'])
                return Response('Resource already exists', status=422)
            except Site.DoesNotExist:
                pass  # Expected
            site = serializer.save()
            if not request.user.details.has_repo_access(site):
                message = 'Current user does not have permission for this repo'
                logger.warn(message + ' | %s | %s', request.user, site)
                return Response(message, status=status.HTTP_403_FORBIDDEN)

        retrieve_franklin_config_file(site, request.user)

        # Call Github to register a webhook
        webhook_r = create_repo_webhook(site, request.user)
        if (status.is_success(webhook_r.status_code) or
                webhook_r.status_code == 422):
            site.webhook_id = webhook_r.json().get('id', '')
            # Call Github to register a deploy key
            deploy_key_r = create_repo_deploy_key(site, request.user)
            if (status.is_success(deploy_key_r.status_code) or
                    deploy_key_r.status_code == 422):
                site.deploy_key_id = deploy_key_r.json().get('id', '')
                site.save()
                return Response(status=status.HTTP_201_CREATED)
        delete_site(site, request.user)
    return Response(status=status.HTTP_400_BAD_REQUEST)


def retrieve_franklin_config_file(site, user):
    # Call Github for franklin config
    config = get_franklin_config(site, user)
    if config and not hasattr(config, 'status_code'):
        # Optional. Update DB with any relevant .franklin config
        pass


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
        site.is_active = False
        site.save()
        return delete_site(site, request.user)


def delete_site(site, user):
    # TODO - This should be an async process that is thrown into a queue. The
    # site is no longer active, so the actual delete can occur lazily.
    webhook_delete_response = delete_webhook(site, user)
    if status.is_success(webhook_delete_response.status_code):
        deploy_key_delete_response = delete_deploy_key(site, user)
        if status.is_success(deploy_key_delete_response.status_code):
            site.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            message = 'Github deleted the webhook, but not the deploy key'
            logger.warn(message + ' | %s | %s', user, site)
    return Response(status=status.HTTP_400_BAD_REQUEST)


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
