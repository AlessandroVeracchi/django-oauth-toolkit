from __future__ import unicode_literals

from django.test import TestCase, RequestFactory
from django.core.urlresolvers import reverse

from ..compat import urlparse, parse_qs, urlencode, get_user_model
from ..models import Application
from ..settings import oauth2_settings
from ..views import ProtectedResourceView


# mocking a protected resource view
class ResourceView(ProtectedResourceView):
    def get(self, request, *args, **kwargs):
        return "This is a protected resource"


class BaseTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.test_user = get_user_model().objects.create_user("test_user", "test@user.com", "123456")
        self.dev_user = get_user_model().objects.create_user("dev_user", "dev@user.com", "123456")

        self.application = Application(
            name="Test Implicit Application",
            redirect_uris="http://localhost http://example.com http://example.it",
            user=self.dev_user,
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_IMPLICIT,
        )
        self.application.save()

        oauth2_settings.SCOPES = ['read', 'write']

    def tearDown(self):
        self.application.delete()
        self.test_user.delete()
        self.dev_user.delete()


class TestImplicitAuthorizationCodeView(BaseTest):
    def test_pre_auth_valid_client(self):
        """
        Test response for a valid client_id with response_type: token
        """
        self.client.login(username="test_user", password="123456")

        query_string = urlencode({
            'client_id': self.application.client_id,
            'response_type': 'token',
            'state': 'random_state_string',
            'scope': 'read write',
            'redirect_uri': 'http://example.it',
        })
        url = "{url}?{qs}".format(url=reverse('authorize'), qs=query_string)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # check form is in context and form params are valid
        self.assertIn("form", response.context)

        form = response.context.get("form")
        self.assertEqual(form['redirect_uri'].value(), "http://example.it")
        self.assertEqual(form['state'].value(), "random_state_string")
        self.assertEqual(form['scopes'].value(), "read write")
        self.assertEqual(form['client_id'].value(), self.application.client_id)

    def test_pre_auth_invalid_client(self):
        """
        Test error for an invalid client_id with response_type: token
        """
        self.client.login(username="test_user", password="123456")

        query_string = urlencode({
            'client_id': 'fakeclientid',
            'response_type': 'token',
        })
        url = "{url}?{qs}".format(url=reverse('authorize'), qs=query_string)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_pre_auth_default_redirect(self):
        """
        Test for default redirect uri if omitted from query string with response_type: token
        """
        self.client.login(username="test_user", password="123456")

        query_string = urlencode({
            'client_id': self.application.client_id,
            'response_type': 'token',
        })
        url = "{url}?{qs}".format(url=reverse('authorize'), qs=query_string)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        form = response.context.get("form")
        self.assertEqual(form['redirect_uri'].value(), "http://localhost")

    def test_pre_auth_forbibben_redirect(self):
        """
        Test error when passing a forbidden redirect_uri in query string with response_type: token
        """
        self.client.login(username="test_user", password="123456")

        query_string = urlencode({
            'client_id': self.application.client_id,
            'response_type': 'token',
            'redirect_uri': 'http://forbidden.it',
        })
        url = "{url}?{qs}".format(url=reverse('authorize'), qs=query_string)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_post_auth_allow(self):
        """
        Test authorization code is given for an allowed request with response_type: token
        """
        self.client.login(username="test_user", password="123456")

        form_data = {
            'client_id': self.application.client_id,
            'state': 'random_state_string',
            'scopes': 'read write',
            'redirect_uri': 'http://example.it',
            'response_type': 'token',
            'allow': True,
        }

        response = self.client.post(reverse('authorize'), data=form_data)
        self.assertEqual(response.status_code, 302)
        self.assertIn('http://example.it#', response['Location'])
        self.assertIn('access_token=', response['Location'])
        self.assertIn('state=random_state_string', response['Location'])

    def test_token_post_auth_deny(self):
        """
        Test error when resource owner deny access
        """
        self.client.login(username="test_user", password="123456")

        form_data = {
            'client_id': self.application.client_id,
            'state': 'random_state_string',
            'scopes': 'read write',
            'redirect_uri': 'http://example.it',
            'response_type': 'token',
            'allow': False,
        }

        response = self.client.post(reverse('authorize'), data=form_data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=access_denied", response['Location'])


class TestImplicitTokenView(BaseTest):
    def test_resource_access_allowed(self):
        self.client.login(username="test_user", password="123456")

        # retrieve a valid authorization code
        authcode_data = {
            'client_id': self.application.client_id,
            'state': 'random_state_string',
            'scopes': 'read write',
            'redirect_uri': 'http://example.it',
            'response_type': 'token',
            'allow': True,
        }
        response = self.client.post(reverse('authorize'), data=authcode_data)
        # within implicit grant, access token is in the url fragment
        frag_dict = parse_qs(urlparse(response['Location']).fragment)
        access_token = frag_dict['access_token'].pop()

        # use token to access the resource
        auth_headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token,
        }
        request = self.factory.get("/fake-resource", **auth_headers)
        request.user = self.test_user

        view = ResourceView.as_view()
        response = view(request)
        self.assertEqual(response, "This is a protected resource")