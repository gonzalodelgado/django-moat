"""
Mini-app to globally prevent access to site with Basic Auth. Emulates
setting up HTTP Basic Auth in your webserver (Apache, nginx).

If you provide a HTTP_AUTH_REALM in your settings, that will be used as
the realm for the challenge.

Adapted from:
    - HTTP Authorization Middleware/Decorator
        http://djangosnippets.org/snippets/1720/
    - django-privatebetaa
        https://github.com/pragmaticbadger/django-privatebeta
"""

import base64
import logging
import re

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib.auth import authenticate
from django.core.urlresolvers import resolve


LOG = logging.getLogger(__name__)


class HttpResponseTemporaryRedirect(HttpResponseRedirect):
    status_code = 307


class MoatMiddleware(object):
    """
    Some middleware to authenticate all requests at this site.
    """
    def __init__(self):
        self.always_allow_modules = getattr(
            settings, 'MOAT_ALWAYS_ALLOW_MODULES', [])
        self.always_allow_views = getattr(
            settings, 'MOAT_ALWAYS_ALLOW_VIEWS', [])
        self.always_allow_urls = map(
            re.compile,
            getattr(settings, 'MOAT_ALWAYS_ALLOW_URLS', []))
        self.allow_admin = getattr(settings, 'MOAT_ALLOW_ADMIN', False)
        self.debug_disable_https = getattr(
            settings, 'MOAT_DEBUG_DISABLE_HTTPS', False)

    def process_request(self, request):
        # check if its globally disabled
        try:
            if not settings.MOAT_ENABLED:
                return
        except AttributeError:
            pass

        for allowed_url in self.always_allow_urls:
            if allowed_url.search(request.path):
                LOG.debug('URL %s is allowed by config', request.path)
                return

        # see if we already authenticated
        if request.session.get('moat_username') is not None:
            LOG.info("Already authenticated as: %s",
                     request.session['moat_username'])
            return
        else:
            LOG.debug("Didn't find moat auth in session")

        whitelisted_modules = []
        if self.always_allow_modules:
            whitelisted_modules += self.always_allow_modules

        whitelisted_views = ['django.views.generic.simple.redirect_to']
        if self.always_allow_views:
            whitelisted_views += self.always_allow_views

        view_func = resolve(request.META.get('PATH_INFO')).func
        full_view_name = '%s.%s' % (view_func.__module__, view_func.__name__)
        LOG.debug("full_view_name = %s", full_view_name)

        if full_view_name in whitelisted_views:
            return
        if '%s' % view_func.__module__ in whitelisted_modules:
            return
        if self.allow_admin and view_func.__module__.startswith(
                'django.contrib.admin'):
            return

        # Check for "cloud" HTTPS environments
        # adapted from http://djangosnippets.org/snippets/2472/
        if 'HTTP_X_FORWARDED_PROTO' in request.META:
            if request.META['HTTP_X_FORWARDED_PROTO'] == 'https':
                request.is_secure = lambda: True

        # if not, redirect to secure
        if not self.debug_disable_https and not request.is_secure():
            return self._redirect(request)

        # finally check auth
        return self._http_auth_helper(request)

    def _redirect(self, request):
        newurl = "https://%s%s" % (request.get_host(), request.get_full_path())
        if settings.DEBUG and request.method == 'POST':
            raise RuntimeError(
                "Django can't perform a SSL redirect while maintaining POST "
                "data. Please structure your views so that redirects only "
                "occur during GETs.")

        return HttpResponseTemporaryRedirect(newurl)

    def _http_auth_helper(self, request):
        # At this point, the user is either not logged in, or must log
        # in using http auth.  If they have a header that indicates a
        # login attempt, then use this to try to login.
        if 'HTTP_AUTHORIZATION' in request.META:
            auth = request.META['HTTP_AUTHORIZATION'].split()
            if len(auth) == 2:
                if auth[0].lower() == 'basic':
                    # Currently, only basic http auth is used.
                    uname, passwd = base64.b64decode(auth[1]).split(':')
                    user = authenticate(username=uname, password=passwd)
                    if user and user.is_staff:
                        request.session['moat_username'] = uname
                        return

        # The username/password combo was incorrect, or not provided.
        # Challenge the user for a username/password.
        resp = HttpResponse()
        resp.status_code = 401
        try:
            # If we have a realm in our settings, use this for the
            # challenge.
            realm = settings.HTTP_AUTH_REALM
        except AttributeError:
            realm = ""

        resp['WWW-Authenticate'] = 'Basic realm="%s"' % realm
        return resp
