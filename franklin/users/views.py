from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.views.generic import TemplateView
from django.shortcuts import redirect, render

from social.apps.django_app.utils import psa

from .helpers import LoginRequiredMixin


class UserLogin(TemplateView):
    """ Login with GitHub

    Extends: TemplateView
    """
    template_name = 'login.html'

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated():
            return redirect('user:dashboard')
        next = request.GET.get('next')
        if not next:
            next = reverse('user:dashboard')
        return render(request, self.template_name, 
                      self.get_context_data(next=next))


class UserDashboard(LoginRequiredMixin, TemplateView):
    """ Placeholder for the future dashboard we will create

    Extends: TemplateView
    """
    template_name = 'dashboard.html'
