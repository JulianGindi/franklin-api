import logging
from django.conf import settings
from django.dispatch import receiver
from django.db import models
from django.db.models.signals import post_save
from django.utils.translation import ugettext as _

from builder.helpers import make_rest_get_call 

github_base = 'https://api.github.com/'
logger = logging.getLogger(__name__)


class UserDetails(models.Model):
    """ Extra details and functions attached to the default user created with
    github social signin

    :param user: FK to a unique user
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name='details')

    def get_github_id(self):
        social = self.user.social_auth.get(provider='github')
        if social:
            return social.uid
        return None

    def get_user_repos(self):
        # TODO - This call is somewhat inefficient and we currently access this
        # data multiple times. Either store it in the DB (raw?) or cache it
        # somehow so we aren't making constant calls to github.
        social = self.user.social_auth.get(provider='github')
        have_next_page = True
        url = github_base + 'user/repos?per_page=100'
        # TODO - Confirm that a header token is the best/most secure way to go
        headers = {
                    'content-type': 'application/json',
                    'Authorization': 'token ' + social.extra_data['access_token']
                  }
        repos = []

        while have_next_page:
            response = None
            have_next_page = False # when in doubt, we'll leave the loop after 1
            response = make_rest_get_call(url, headers)

            if response is not None:
                # Add all of the repos to our list
                for repo in response.json():
                    repo_data = {}
                    repo_data['id'] = repo['id']
                    repo_data['name'] = repo['name']
                    repo_data['url'] = repo['html_url']
                    repo_data['owner'] = {}
                    repo_data['owner']['name'] = repo['owner']['login']
                    repo_data['owner']['id'] = repo['owner']['id']
                    repos.append(repo_data)

                # If the header has a paging link called 'next', update our url
                # and continue with the while loop
                if response.links and response.links.get('next', None):
                    url = response.links['next']['url']
                    have_next_page = True

        if not repos:
            logger.error('Failed to find repos for user', user.username)
        return repos

    def __str__(self):
        return self.user.username
    
    class Meta(object):
        verbose_name = _('Detail')
        verbose_name_plural = _('Details')


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_details_for_new_user(sender, created, instance, **kwargs):
    if created:
        UserDetails.objects.create(user=instance)
